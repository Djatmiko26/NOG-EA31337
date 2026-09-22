"""All replay specimens are synthetic; no market fetch, old data or SQLite writes."""
import copy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from replay_common import canonical, read_json, write_new
from replay_snapshot import Bar, create_snapshot, load_snapshot, save_snapshot, verify_snapshot, main
from replay_split import Segment, split_chronological, validate_plan


def snapshot(n=800, direction='BUY', prior_use='SYNTHETIC_TEST'):
    # Oscillation gives genuine pullbacks to the unchanged indicator evaluator.
    import math
    sign = 1 if direction == 'BUY' else -1
    rows = []
    for i in range(n):
        close = 4400 + sign * (.25*i + 3*math.sin(i*.45))
        rows.append(dict(time=1800000000+i*300, open=close-sign*.7,
                         high=close+1, low=close-1, close=close, spread=20, tick_volume=100+i%17))
    return create_snapshot(rows, source_identity_hash='a'*64, point=.01,
        settings={'fixture': 'synthetic-m3-v1'}, created_at='2026-09-22T00:00:00+00:00',
        closed_before_raw=rows[-1]['time']+300, generator_hash='b'*64, prior_use=prior_use)


class SnapshotTests(unittest.TestCase):
    def test_roundtrip_and_windows_file_handles_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'snapshot.json'
            original = snapshot(10)
            save_snapshot(path, original)
            self.assertEqual(load_snapshot(path), original)
            # Windows rename/removal fails for a leaked open file handle.
            path.rename(path.with_suffix('.moved'))
        self.assertFalse(Path(directory).exists())

    def test_no_overwrite_even_empty_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'snapshot.json'
            path.write_bytes(b'')
            with self.assertRaises(FileExistsError):
                save_snapshot(path, snapshot(10))
            self.assertEqual(path.read_bytes(), b'')

    def test_corruption_content_and_each_manifest_field(self):
        original = snapshot(10).to_dict()
        for field, value in (('content_sha256', '0'*64), ('settings_sha256', '0'*64),
                             ('snapshot_id', '0'*64), ('bar_count', 11), ('symbol', 'EURUSD'),
                             ('generator_version', 'other'), ('first_bar_raw', 1)):
            document = copy.deepcopy(original)
            document['manifest'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_snapshot(document)
        document = copy.deepcopy(original)
        document['bars'][0]['tick_volume'] += 1
        with self.assertRaises(ValueError):
            verify_snapshot(document)

    def test_settings_are_detached(self):
        value = snapshot(10)
        detached = value.manifest
        detached['settings']['fixture'] = 'changed'
        self.assertEqual(value.manifest['settings']['fixture'], 'synthetic-m3-v1')
        with self.assertRaises(Exception):
            value.bars[0].close = 1

    def test_closed_bars_only(self):
        document = snapshot(10).to_dict()
        document['manifest']['closed_before_raw'] -= 1
        with self.assertRaisesRegex(ValueError, 'FORMING_OR_FUTURE'):
            verify_snapshot(document)

    def test_duplicate_or_reverse_bars_rejected(self):
        for rows in ([asdict(snapshot(1).bars[0])]*2, list(reversed(snapshot(10).to_dict()['bars']))):
            document = snapshot(10).to_dict()
            document['bars'] = rows
            with self.assertRaisesRegex(ValueError, 'UNORDERED_OR_DUPLICATE'):
                verify_snapshot(document)

    def test_invalid_ohlc_nonfinite_and_boolean_rejected(self):
        for field, value in (('high', 1), ('close', float('nan')), ('spread', True), ('tick_volume', -1)):
            row = asdict(snapshot(1).bars[0]); row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                Bar(**row)

    def test_strict_json_and_crlf_portability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'snapshot.json'
            path.write_bytes(canonical(snapshot(10).to_dict()).replace(b'\n', b'\r\n'))
            self.assertEqual(load_snapshot(path), snapshot(10))
            for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    read_json(path)

    def test_cli_create_verify_describe_read_only(self):
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory)
            raw, settings, out = root/'bars.json', root/'settings.json', root/'snapshot.json'
            write_new(raw, canonical(snapshot(10).to_dict()['bars']))
            write_new(settings, canonical({'fixture':'CLI'}))
            self.assertEqual(main(['--create', str(raw), '--output', str(out), '--source-hash', 'a'*64,
                '--point', '.01', '--settings', str(settings), '--created-at', '2026-09-22T00:00:00+00:00',
                '--closed-before-raw', str(snapshot(10).bars[-1].time+300)]), 0)
            before = {p.name:p.read_bytes() for p in root.iterdir()}
            self.assertEqual(main(['--verify', str(out)]), 0)
            self.assertEqual(main(['--describe', str(out)]), 0)
            self.assertEqual(before, {p.name:p.read_bytes() for p in root.iterdir()})

    def test_output_into_repo_data_forbidden_before_write(self):
        from replay_common import ROOT
        with self.assertRaisesRegex(ValueError, 'OUTPUT_FORBIDDEN'):
            save_snapshot(ROOT/'data'/'must-not-exist-m3.json', snapshot(10))


class SplitTests(unittest.TestCase):
    def test_chronological_with_purge_and_disjoint_features_outcomes(self):
        plan = split_chronological(800)
        self.assertEqual([s.label for s in plan.segments], ['DEVELOPMENT','VALIDATION','HOLDOUT'])
        used = []
        for segment in plan.segments:
            indices = list(plan.indices(segment))
            self.assertTrue(indices)
            bars = set()
            for index in indices:
                feature, future = plan.windows(segment,index)
                self.assertEqual(feature[1], future[0])
                self.assertEqual(feature[1]-feature[0], 120)
                self.assertEqual(future[1]-future[0], 20)
                self.assertGreaterEqual(feature[0],segment.start)
                self.assertLessEqual(future[1],segment.stop)
                bars.update(range(feature[0],future[1]))
            for earlier in used:
                self.assertTrue(bars.isdisjoint(earlier))
            used.append(bars)
        for a,b in zip(plan.segments,plan.segments[1:]):
            self.assertGreaterEqual(b.start-a.stop,20)

    def test_short_purge_and_insufficient_history_rejected(self):
        for kwargs in ({'purge':19}, {'horizon':100}, {'development_fraction':.9,'validation_fraction':.2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                split_chronological(800,**kwargs)

    def test_leaking_future_and_history_windows_rejected(self):
        plan = split_chronological(800)
        for segment in plan.segments:
            for index in (segment.start,segment.stop-plan.horizon,segment.stop):
                with self.assertRaisesRegex(ValueError,'LEAKAGE'):
                    plan.windows(segment,index)

    def test_forged_overlapping_split_rejected(self):
        plan = split_chronological(800)
        segments = list(plan.segments)
        segments[1] = replace(segments[1],start=segments[0].stop-1)
        with self.assertRaisesRegex(ValueError,'LEAKAGE'):
            validate_plan(replace(plan,segments=tuple(segments)))
