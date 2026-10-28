"""Explicit D0 selection -> append-only JSON. No DB, credentials or HTTP access."""
import argparse
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from . import leader_tracking as tracking

SCHEMA = 'd0-store-v1'
PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = PROJECT / 'data' / 'd0_snapshots'
MAX_DELAY = timedelta(minutes=5)  # Recording policy, not a trading strategy.
EVENT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', re.ASCII)
SENSITIVE = re.compile(r'(?i)secret|token|password|authorization|cookie|app.?key|account|계좌')
CHECKS = ('market_cap_3000_eok', 'closing_turnover_1000_eok', 'turnover_rank_40')


class D0Error(ValueError):
    """Only static, non-sensitive reason codes are exposed by the CLI."""


def now():
    return datetime.now(tracking.KST)


def fingerprint(value):
    return hashlib.sha256(tracking.canonical(value).encode('utf-8')).hexdigest()


def no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE.search(str(key)):
                raise D0Error('sensitive_field_rejected')
            no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            no_secrets(item)
    elif isinstance(value, str):
        if re.search(r'(?i)-----BEGIN .*PRIVATE KEY|\bBearer\s+|\bgh[pousr]_[A-Za-z0-9]{20,}', value):
            raise D0Error('sensitive_value_rejected')


@dataclass(frozen=True)
class SelectionSnapshot(tracking.Snapshot):
    selection_event_id: str = ''

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.selection_event_id, str) or not EVENT_ID.fullmatch(self.selection_event_id):
            raise D0Error('valid_selection_event_id_required')

    @property
    def episode_id(self):
        # Same event in the same environment cannot be reattached to another code/date.
        return fingerprint([SCHEMA, self.environment, self.selection_event_id])

    @property
    def legacy_group_id(self):
        return super().episode_id


def timely(selected_at, at):
    if selected_at.date() != at.date() or not timedelta(0) <= at - selected_at <= MAX_DELAY:
        raise D0Error('backdated_or_stale_selection_forbidden')


def observation(data):
    if data is None:
        return None
    data = dict(data)
    data['trade_date'] = date.fromisoformat(data['trade_date'])
    bar = tracking.Observation(**data)
    no_secrets(json.loads(bar.raw_json))
    return bar


def snapshot_data(snapshot):
    result = {}
    for field in fields(snapshot):
        value = getattr(snapshot, field.name)
        if field.name == 'd0_bar':
            value = ({f.name: getattr(value, f.name) for f in fields(value)} if value else None)
            if value:
                value = {k: v.isoformat() if isinstance(v, (date, datetime)) else str(v)
                         if isinstance(v, Decimal) else v for k, v in value.items()}
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        result[field.name] = value
    return result


def build_record(document, *, at=None):
    """Capture an explicit, current selection supported by an existing ready review.

    The caller supplies an independently recorded selection, not merely a stock
    code. This function never chooses stocks or upgrades evaluation flags.
    """
    at = tracking.timestamp(at or now())
    no_secrets(document)
    event = document.get('selection')
    if not isinstance(event, dict):
        raise D0Error('actual_selection_event_missing')
    required = {'event_id', 'source', 'status', 'stock_code', 'environment', 'market',
                'selected_at', 'evaluation_sha256', 'reasons'}
    if set(event) != required or event['status'] != 'selected_for_observation':
        raise D0Error('explicit_selection_event_required')
    if not isinstance(event['event_id'], str) or not EVENT_ID.fullmatch(event['event_id']):
        raise D0Error('valid_selection_event_id_required')
    if not isinstance(event['stock_code'], str) or not re.fullmatch(r'[0-9A-Z]{6}', event['stock_code'], re.ASCII):
        raise D0Error('exact_krx_stock_code_string_required')
    if not isinstance(event['source'], str) or not event['source'].strip():
        raise D0Error('selection_source_missing')
    if (not isinstance(event['reasons'], list) or not event['reasons']
            or not all(isinstance(r, str) and r.strip() for r in event['reasons'])):
        raise D0Error('selection_reasons_missing')
    selected = tracking.timestamp(event['selected_at'])
    timely(selected, at)
    report = document.get('evaluation')
    if not isinstance(report, dict) or fingerprint(report) != event['evaluation_sha256']:
        raise D0Error('evaluation_binding_mismatch')
    rows = [r for r in report.get('rows', []) if r.get('stock_code') == event['stock_code']]
    if len(rows) != 1:
        raise D0Error('one_matching_evaluation_row_required')
    row = rows[0]
    qualification = row.get('qualification') or {}
    if row.get('candidate_confirmed') is not False or qualification.get('candidate_confirmed') is not False:
        raise D0Error('candidate_must_remain_unconfirmed')
    if (qualification.get('final_review_allowed') is not True or qualification.get('reasons') != []
            or qualification.get('sector_co_rise') is not True
            or not all(qualification.get('checks', {}).get(k) is True for k in CHECKS)):
        raise D0Error('evaluation_not_ready')
    reference = tracking.timestamp((report.get('capital_integration') or {}).get('reference_at'))
    if reference.date() != selected.date() or not timedelta(0) <= selected - reference <= MAX_DELAY:
        raise D0Error('evaluation_time_not_current')
    sources = report.get('sources') or {}
    for key, api in (('ranking', 'ka00198'), ('turnover', 'ka10032')):
        source = sources.get(key) or {}
        if source.get('api_id') != api or source.get('environment') != event['environment']:
            raise D0Error('evaluation_source_identity_mismatch')
        if source.get('status') != 'success' or source.get('test_mode') is not False:
            raise D0Error('non_test_successful_sources_required')
        if not source.get('id') or not source.get('source_kind'):
            raise D0Error('source_identity_missing')
        if source.get('source_kind') in ('fixture', 'synthetic'):
            raise D0Error('fixture_source_not_a_real_selection')
        start = tracking.timestamp(source.get('request_started_at'))
        end = tracking.timestamp(source.get('response_received_at'))
        if start.date() != selected.date() or not start <= end <= reference:
            raise D0Error('source_chronology_invalid')
        if (source.get('market') or {}).get('stex_tp') != '1' or event['market'] != 'KRX':
            raise D0Error('verified_krx_context_required')
    if sources['turnover'].get('pagination_complete') is not True:
        raise D0Error('turnover_source_incomplete')
    capital = row.get('capital_reference') or {}
    if capital.get('api_id') != 'ka10001' or capital.get('environment') != event['environment']:
        raise D0Error('capital_source_identity_mismatch')
    if not capital.get('source_fingerprint') or row.get('market_cap_won_verified') is None:
        raise D0Error('verified_capital_reference_required')
    cap_start = tracking.timestamp(capital.get('request_started_at'))
    cap_end = tracking.timestamp(capital.get('response_received_at'))
    if cap_start.date() != selected.date() or not cap_start <= cap_end <= reference:
        raise D0Error('capital_chronology_invalid')
    base = tracking.snapshot_from_review(report, stock_code=event['stock_code'],
        captured_at=selected, created_at=at, market=event['market'],
        stock_name=row.get('stock_name'), primary_sector=row.get('primary_sector'),
        d0_bar=observation(document.get('d0_bar')))
    snapshot = SelectionSnapshot(**{f.name: getattr(base, f.name) for f in fields(base)},
                                 selection_event_id=event['event_id'])
    if snapshot.environment != event['environment']:
        raise D0Error('selection_environment_mismatch')
    evidence = document.get('reference_evidence')
    if snapshot.d0_bar and (not isinstance(evidence, str) or not evidence.strip()):
        raise D0Error('d0_reference_evidence_required')
    event = dict(event, selected_at=selected.isoformat())
    record = dict(schema_version=SCHEMA, episode_id=snapshot.episode_id,
        legacy_group_id=snapshot.legacy_group_id, selection=event,
        evaluation=json.loads(tracking.canonical(report)), snapshot=snapshot_data(snapshot),
        reference_price=dict(value=str(snapshot.d0_bar.close) if snapshot.d0_bar and snapshot.d0_bar.close else None,
            status='verified_d0_close' if snapshot.d0_bar and snapshot.d0_bar.close else 'unknown',
            evidence=evidence if snapshot.d0_bar else None), candidate_confirmed=False)
    record['integrity_sha256'] = fingerprint(record)
    return record


def validate_record(record):
    try:
        if record.get('schema_version') != SCHEMA:
            raise D0Error('unsupported_store_schema')
        body = {k: v for k, v in record.items() if k != 'integrity_sha256'}
        if fingerprint(body) != record.get('integrity_sha256'):
            raise D0Error('record_checksum_mismatch')
        rebuilt = build_record(dict(selection=record['selection'], evaluation=record['evaluation'],
            d0_bar=record['snapshot']['d0_bar'], reference_evidence=record['reference_price']['evidence']),
            at=record['snapshot']['created_at'])
        if rebuilt != record:
            raise D0Error('record_model_mismatch')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise D0Error('corrupt_d0_record') from None


def load_record(path):
    path = Path(path)
    if path.is_symlink():
        raise D0Error('symlink_record_forbidden')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        validate_record(record)
        if path.stem != record['episode_id']:
            raise D0Error('record_filename_mismatch')
        return record
    except (OSError, ValueError, TypeError) as exc:
        raise D0Error('corrupt_or_unreadable_d0_record') from None


def load_snapshot(path):
    """Return a tracking-compatible per-selection Snapshot without changing history."""
    data = dict(load_record(path)['snapshot'])
    data['d0'] = date.fromisoformat(data['d0'])
    data['d0_bar'] = observation(data['d0_bar'])
    return SelectionSnapshot(**data)


def store_root(path):
    supplied = Path(path).absolute()
    if any(p.is_symlink() for p in (supplied, *supplied.parents)):
        raise D0Error('symlink_store_forbidden')
    root = supplied.resolve()
    if root == PROJECT or PROJECT in root.parents:
        if root != DEFAULT_ROOT and DEFAULT_ROOT not in root.parents:
            raise D0Error('inside_project_use_data_d0_snapshots_only')
    if root.exists() and not root.is_dir():
        raise D0Error('store_directory_required')
    return root


def records(path=DEFAULT_ROOT):
    root = store_root(path)
    return tuple(load_record(p) for p in sorted(root.glob('*.json'))) if root.exists() else ()


def save_record(record, *, root=DEFAULT_ROOT, at=None):
    record = json.loads(tracking.canonical(record))
    validate_record(record)
    current = tracking.timestamp(at or now())
    timely(tracking.timestamp(record['selection']['selected_at']), current)
    if tracking.timestamp(record['snapshot']['created_at']) > current:
        raise D0Error('future_record_creation')
    root = store_root(root)
    records(root)  # Refuse to write into a store containing a corrupt record.
    target = root / (record['episode_id'] + '.json')
    if target.exists():
        raise D0Error('duplicate_selection_event')
    root.mkdir(parents=True, exist_ok=True)
    data = (tracking.canonical(record) + '\n').encode('utf-8')
    pending = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.pending-', suffix='.tmp', dir=root, delete=False) as handle:
            pending = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(pending, target)  # Atomic publication; never replaces an existing target.
        except FileExistsError:
            raise D0Error('duplicate_selection_event') from None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)  # Only this call's temporary file.
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='Explicit selection event + bound existing evaluation JSON')
    parser.add_argument('--store-dir', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--save', action='store_true', help='Publish one current event; never overwrite')
    args = parser.parse_args(argv)
    try:
        existing = records(args.store_dir)
        if args.input is None:
            print(tracking.canonical(dict(mode='read_only', existing_records=len(existing), planned=0, saved=0,
                reason='actual_selection_event_missing')))
            return 0
        document = json.loads(args.input.read_text(encoding='utf-8-sig'))
        current = now()
        record = build_record(document, at=current)
        if any(r['episode_id'] == record['episode_id'] for r in existing):
            raise D0Error('duplicate_selection_event')
        snapshot = record['snapshot']
        preview = dict(episode_id=record['episode_id'], event_id=record['selection']['event_id'],
            stock_code=snapshot['stock_code'], environment=snapshot['environment'], market=snapshot['market'],
            d0=snapshot['d0'], selected_at=snapshot['captured_at'], created_at=snapshot['created_at'],
            selection_reasons=record['selection']['reasons'], reference_price=record['reference_price'],
            candidate_confirmed=False, schema_version=SCHEMA)
        if args.save:
            save_record(record, root=args.store_dir, at=current)
        print(tracking.canonical(dict(mode='saved' if args.save else 'read_only_preview',
            existing_records=len(existing), planned=1, saved=1 if args.save else 0, preview=preview)))
        return 0
    except D0Error as exc:
        print(tracking.canonical(dict(status='blocked', saved=0, reason=str(exc))))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(tracking.canonical(dict(status='blocked', saved=0, reason='invalid_input_or_storage_failure')))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
