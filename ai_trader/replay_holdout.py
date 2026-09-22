"""Append-only, single-evaluation holdout reservations. No old journal access."""
from pathlib import Path
from replay_common import canonical, digest, output_path, read_json, write_new
from replay_split import validate_plan


def permit_fields(snapshot, plan, settings_hash, versions_hash):
    validate_plan(plan)
    m = snapshot.manifest
    segment = plan.segments[-1]
    return dict(schema_version='holdout-reservation-v1', snapshot_id=m['snapshot_id'],
                content_sha256=m['content_sha256'], source_identity_hash=m['source_identity_hash'],
                symbol=m['symbol'], timeframe=m['timeframe'],
                first_bar_raw=snapshot.bars[segment.start].time,
                last_bar_raw=snapshot.bars[segment.stop-1].time,
                settings_sha256=settings_hash, versions_sha256=versions_hash)


def reserve_holdout(snapshot, plan, settings_hash, versions_hash, registry, *,
                    declaration, exposure_ranges, allow_synthetic=False):
    m = snapshot.manifest
    synthetic = m['prior_use'] == 'SYNTHETIC_TEST' and allow_synthetic
    if not synthetic and m['prior_use'] != 'DECLARED_UNSEEN':
        raise ValueError('CLEAN_HOLDOUT_NOT_ESTABLISHED')
    if not isinstance(declaration, str) or not declaration.strip():
        raise ValueError('HOLDOUT_PROVENANCE_DECLARATION_REQUIRED')
    fields = permit_fields(snapshot, plan, settings_hash, versions_hash)
    def overlaps(row):
        return (row['symbol'], row['timeframe']) == (fields['symbol'], fields['timeframe']) and max(
            row['first_bar_raw'], fields['first_bar_raw']) <= min(row['last_bar_raw'], fields['last_bar_raw'])
    # Synthetic fixtures are explicitly not claims of a clean market holdout.
    if not synthetic and any(overlaps(row) for row in exposure_ranges):
        raise ValueError('HOLDOUT_OVERLAPS_KNOWN_EXPLORATION')
    registry = output_path(registry)
    registry.mkdir(parents=True, exist_ok=True)
    for path in sorted(registry.glob('*.json')):
        saved = read_json(path)
        if saved.get('sha256') != digest(saved.get('body')):
            raise ValueError('CORRUPTED_HOLDOUT_REGISTRY')
        body = saved['body']
        if body.get('schema_version') != 'holdout-reservation-v1':
            raise ValueError('UNKNOWN_HOLDOUT_REGISTRY_RECORD')
        if overlaps(body):
            raise ValueError('HOLDOUT_ALREADY_EVALUATED_OR_RESERVED')
    body = dict(fields, declaration=declaration, prior_use=m['prior_use'],
                exposure_sha256=digest(exposure_ranges), status='RESERVED_FOR_FINAL_EVALUATION')
    # Key excludes settings/source-code versions: changing parameters cannot renew
    # the budget for the same data. Overlap scans cover other saved snapshots.
    key = digest({'source': m['source_identity_hash'], 'content': m['content_sha256']})
    write_new(registry / (key+'.json'), canonical({'body': body, 'sha256': digest(body)}))
    return fields


def validate_permit(permit, snapshot, plan, settings_hash, versions_hash):
    if permit != permit_fields(snapshot, plan, settings_hash, versions_hash):
        raise ValueError('MATCHED_HOLDOUT_RESERVATION_REQUIRED')
