"""Immutable CLOSED-BID-bar snapshots from LOCAL JSON only. No market-data SDK."""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from replay_common import canonical, digest, finite, integer, read_json, sha256, write_new

VERSION = 'closed-m5-snapshot-v1'
FIELDS = {'time', 'open', 'high', 'low', 'close', 'spread', 'tick_volume'}


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    spread: int
    tick_volume: int

    def __post_init__(self):
        integer(self.time, 1)
        integer(self.spread)
        integer(self.tick_volume)
        for value in (self.open, self.high, self.low, self.close):
            if finite(value) == 0:
                raise ValueError('NONPOSITIVE_PRICE')
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError('INVALID_OHLC')


def bars_from(rows, closed_before_raw):
    integer(closed_before_raw, 1)
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError('BARS_REQUIRED')
    bars = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('UNEXPECTED_BAR_FIELDS')
        bar = Bar(**row)
        if bars and bar.time <= bars[-1].time:
            raise ValueError('UNORDERED_OR_DUPLICATE_BARS')
        if bar.time + 300 > closed_before_raw:
            raise ValueError('FORMING_OR_FUTURE_BAR_REJECTED')
        bars.append(bar)
    return tuple(bars)


@dataclass(frozen=True)
class Snapshot:
    manifest_json: str
    bars: tuple[Bar, ...]

    @property
    def manifest(self):
        return json.loads(self.manifest_json)  # detached immutable settings

    def to_dict(self):
        return {'manifest': self.manifest, 'bars': [asdict(b) for b in self.bars]}


def create_snapshot(rows, *, source_identity_hash, point, settings, created_at,
                    closed_before_raw, generator_hash, prior_use='UNKNOWN'):
    sha256(source_identity_hash); sha256(generator_hash)
    if finite(point) == 0:
        raise ValueError('POINT_REQUIRED')
    if type(settings) is not dict:
        raise ValueError('SETTINGS_OBJECT_REQUIRED')
    stamp = datetime.fromisoformat(created_at)
    if stamp.tzinfo is None:
        raise ValueError('CREATED_AT_TIMEZONE_REQUIRED')
    if prior_use not in ('UNKNOWN', 'EXPLORED', 'DECLARED_UNSEEN', 'SYNTHETIC_TEST'):
        raise ValueError('INVALID_PRIOR_USE')
    bars = bars_from(rows, closed_before_raw)
    payload = [asdict(b) for b in bars]
    manifest = dict(schema_version=VERSION, symbol='XAUUSD', timeframe='M5',
                    source_identity_hash=source_identity_hash, point=point,
                    first_bar_raw=bars[0].time, last_bar_raw=bars[-1].time, bar_count=len(bars),
                    content_sha256=digest(payload), settings=settings, settings_sha256=digest(settings),
                    generator_version=VERSION, generator_sha256=generator_hash, created_at=created_at,
                    closed_before_raw=closed_before_raw, price_basis='BID', prior_use=prior_use,
                    time_basis='RAW_SOURCE_EPOCH_NOT_VERIFIED_UTC')
    manifest['snapshot_id'] = digest(manifest)
    return Snapshot(canonical(manifest).decode('utf-8'), bars)


def verify_snapshot(document):
    if type(document) is not dict or set(document) != {'manifest', 'bars'}:
        raise ValueError('INVALID_SNAPSHOT_ENVELOPE')
    m = document['manifest']
    if type(m) is not dict:
        raise ValueError('INVALID_MANIFEST')
    expected = create_snapshot(document['bars'], source_identity_hash=m['source_identity_hash'],
        point=m['point'], settings=m['settings'], created_at=m['created_at'],
        closed_before_raw=m['closed_before_raw'], generator_hash=m['generator_sha256'], prior_use=m['prior_use'])
    if canonical(expected.manifest) != canonical(m):
        raise ValueError('SNAPSHOT_HASH_OR_MANIFEST_MISMATCH')
    return expected


def load_snapshot(path):
    return verify_snapshot(read_json(path))


def save_snapshot(path, snapshot):
    verified = verify_snapshot(snapshot.to_dict())
    return write_new(path, canonical(verified.to_dict()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--create', type=Path, metavar='LOCAL_BARS_JSON')
    mode.add_argument('--verify', type=Path)
    mode.add_argument('--describe', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source-hash')
    parser.add_argument('--point', type=float)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--created-at')
    parser.add_argument('--closed-before-raw', type=int)
    parser.add_argument('--prior-use', default='UNKNOWN', choices=('UNKNOWN', 'EXPLORED', 'DECLARED_UNSEEN', 'SYNTHETIC_TEST'))
    args = parser.parse_args(argv)
    if args.create:
        if any(v is None for v in (args.output, args.source_hash, args.point, args.settings, args.created_at, args.closed_before_raw)):
            parser.error('--create requires output/source-hash/point/settings/created-at/closed-before-raw')
        raw = read_json(args.create)
        # Explicit local adapter for existing execution snapshots; source identity
        # and closedness still require caller-supplied provenance, never guessed.
        if isinstance(raw, dict):
            raw = raw.get('body', raw)['bars']
        snapshot = create_snapshot(raw, source_identity_hash=args.source_hash, point=args.point,
            settings=read_json(args.settings), created_at=args.created_at, closed_before_raw=args.closed_before_raw,
            generator_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), prior_use=args.prior_use)
        save_snapshot(args.output, snapshot)
    else:
        snapshot = load_snapshot(args.verify or args.describe)
    print(canonical(snapshot.manifest).decode(), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
