"""Read-only AM input preparation. Never opens Store, credentials or transports.

Stored collection start times and specification-only units are not proof of
market timestamps or units. An independently reviewed manifest must bind each
usable record to its source SHA-256 and exact record fingerprint.
"""
import argparse
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from . import am_momentum as am, am_briefing as briefing
from .cross_adapter import readonly_database
from . import am_time_policy as timing
from .am_candidate_adapter import normalize_code


def fingerprint(record):
    return hashlib.sha256(am.canonical(record).encode('utf-8')).hexdigest()


def read_sources(path, *, role='ranking'):
    """Export allowlisted fields only; no raw API bodies, auth or file paths."""
    records = []
    diagnostic_records = []
    with readonly_database(path) as (db, provenance):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table):
            # table is a constant selected below, never user-supplied SQL.
            return [dict(r) for r in db.execute('SELECT * FROM '+table)]
        if role == 'ranking':
            if not {'collections', 'ranking_records'} <= tables:
                raise ValueError('ranking_schema_missing')
            runs = rows('collections'); raw = rows('ranking_records')
            for run in runs:
                fields = ('id', 'collected_at', 'scheduled_at', 'environment', 'api_id', 'qry_tp', 'test_mode', 'status', 'record_count')
                record = dict(kind='ranking', **{k: run.get(k) for k in fields})
                record['stocks'] = sorted([dict(rank=r['rank'], stk_cd=r['stk_cd'], stk_nm=r.get('stk_nm'))
                                            for r in raw if r['collection_id'] == run['id']], key=lambda r: r['rank'])
                records.append(record)
            if {'stock_sectors', 'sectors'} <= tables:
                for r in db.execute('SELECT t.stock_code,s.sector_name,t.updated_at FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id'):
                    records.append(dict(kind='sector', **dict(r)))
            if {'quote_batches', 'quote_observations'} <= tables:
                batches = {r['id']: r for r in rows('quote_batches')}
                ranks = {(r['collection_id'], r['rank']): r['stk_cd'] for r in raw}
                parents = {r['id']: r for r in runs}
                fields = ('batch_id', 'collection_id', 'rank', 'requested_code', 'api_id', 'test_mode', 'status',
                          'request_started_at', 'request_finished_at', 'http_status', 'return_code',
                          'cur_prc', 'flu_rt', 'trde_prica', 'date', 'tm', 'attempt', 'request_id')
                for r in rows('quote_observations'):
                    batch = batches.get(r['batch_id'], {})
                    parent = parents.get(r['collection_id'], {})
                    records.append(dict(kind='quote', **{k: r.get(k) for k in fields},
                                        environment=batch.get('environment'), parent_environment=parent.get('environment'),
                                        batch_collection_id=batch.get('collection_id'), batch_api_id=batch.get('api_id'),
                                        batch_status=batch.get('status'), batch_test_mode=batch.get('test_mode'),
                                        parent_code=ranks.get((r['collection_id'], r['rank'])),
                                        parent_test_mode=parent.get('test_mode')))
        elif role == 'turnover':
            if not {'turnover_runs', 'turnover_rows', 'turnover_pages'} <= tables:
                raise ValueError('turnover_schema_missing')
            runs = {r['id']: r for r in rows('turnover_runs')}
            pages = {(r['run_id'], r['page_no']): r for r in rows('turnover_pages')}
            for r in rows('turnover_rows'):
                run, page = runs.get(r['run_id'], {}), pages.get((r['run_id'], r['page_no']), {})
                raw = json.loads(r['raw_json'])
                records.append(dict(kind='turnover', run_id=r['run_id'], page_no=r['page_no'], row_no=r['row_no'],
                                    environment=run.get('environment'), source_kind=run.get('source_kind'),
                                    api_id=run.get('api_id'), status=run.get('status'), test_mode=run.get('test_mode'),
                                    request_started_at=page.get('started_at'), request_finished_at=page.get('finished_at'),
                                    http_status=page.get('http_status'), return_code=page.get('return_code'),
                                    validation_status=r.get('validation_status'),
                                    request_profile=json.loads(run.get('request_json', '{}')),
                                    stock_code=raw.get('stk_cd'), stored_code=r.get('stock_code'),
                                    trde_prica=raw.get('trde_prica'), flu_rt=raw.get('flu_rt'), cur_prc=raw.get('cur_prc')))
                # Preserve 0186 rank for diagnostics without changing the reviewed
                # metric record contract/fingerprint used by existing manifests.
                diagnostic_records.append(dict(records[-1], now_rank=raw.get('now_rank'),
                                               stored_turnover_rank=r.get('turnover_rank')))
        else:
            raise ValueError('unsupported_source_role')
    return dict(role=role, sha256=provenance['sha256'], hash_unchanged=provenance['hash_unchanged'],
                sqlite_mode=provenance['sqlite_mode'], records=records,
                diagnostic_records=diagnostic_records if role == 'turnover' else records)


def read_capital(path):
    """Read an explicit export of existing market_cap.parse_response records."""
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if document.get('source_kind') not in ('collected_export', 'fixture'):
        raise ValueError('capital_export_source_required')
    fields = ('api_id', 'environment', 'requested_code', 'request_started_at', 'response_received_at',
              'http_status', 'return_code', 'issues')
    records = []
    for item in document.get('records', []):
        records.append(dict(kind='capital', **{k: item.get(k) for k in fields},
                            source_kind=document['source_kind'], stock_code=item.get('raw', {}).get('stk_cd'),
                            mac=item.get('raw', {}).get('mac')))
    if Path(path).read_bytes() != raw:
        raise ValueError('capital_source_changed')
    return dict(role='capital', sha256=hashlib.sha256(raw).hexdigest(), hash_unchanged=True, records=records)


def quote_mode_valid(record, window):
    if not window:
        return record.get('batch_test_mode')==1 and record.get('test_mode')==1
    mode=record.get('test_mode')
    return (type(mode) is int and mode in (0,1) and record.get('batch_test_mode')==mode
            and (mode==1 or record.get('parent_test_mode')==0))


def adapt(ranking_db, *, trade_date, cutoff_at, environment, evidence=None,
          turnover_db=None, capital_json=None, test_mode=False, synthetic=False,
          observation_policy=None, master_db=None, candidate_filter=False):
    if type(candidate_filter) is not bool or (candidate_filter and master_db is None):
        raise ValueError('candidate_filter_requires_master_db')
    day, cutoff = date.fromisoformat(trade_date), am.instant(cutoff_at)
    if cutoff.date() != day or not '09:20' <= cutoff.strftime('%H:%M') <= '09:50':
        raise ValueError('invalid_adapter_cutoff')
    if environment not in ('mock', 'real') or type(test_mode) is not bool or type(synthetic) is not bool:
        raise ValueError('explicit_adapter_scope_required')
    manifest = evidence or {}
    if manifest and (manifest.get('schema_version') != 'am-verification-manifest-v1'
                     or manifest.get('synthetic') is not synthetic):
        raise ValueError('verification_manifest_scope_invalid')
    sources = [read_sources(ranking_db)]
    if turnover_db is not None: sources.append(read_sources(turnover_db, role='turnover'))
    if capital_json is not None: sources.append(read_capital(capital_json))
    source = 'fixture' if synthetic else 'readonly_adapter'
    document = dict(schema_version=am.INPUT_SCHEMA if synthetic else am.ADAPTER_INPUT_SCHEMA,
                    source_kind=source, trade_date=trade_date, environment=environment, market='KRX',
                    selection_cutoff_at=cutoff.isoformat(), rankings=[], securities={})
    if observation_policy is not None:
        document['observation_policy']=observation_policy
    window=timing.from_document(document)
    deadline=window.deadline(cutoff) if window else cutoff
    diagnostics, usable_records = [], []
    scope = dict(source_kind=source, environment=environment, market='KRX')

    def reject(record, reason):
        diagnostics.append(dict(kind=record['kind'], record_fingerprint=fingerprint(record), reason=reason))

    def proof_for(record, sha):
        proof = manifest.get('records', {}).get(fingerprint(record))
        if not proof or proof.get('source_sha256') != sha or proof.get('verified') is not True:
            reject(record, 'bound_verification_missing'); return None
        if (proof.get('environment') != environment or proof.get('market') != 'KRX'
                or proof.get('trade_date') != trade_date or proof.get('market_verified') is not True
                or proof.get('market_time_verified') is not True):
            reject(record, 'verification_scope_mismatch'); return None
        observed, received = am.timestamp(proof.get('observed_at')), am.timestamp(proof.get('received_at'))
        if not timing.available(window,observed,received,cutoff):
            reject(record, 'verification_time_outside_cutoff'); return None
        am.label(proof.get('evidence_reference'))
        stored_end = record.get('request_finished_at') or record.get('response_received_at')
        stored_start = record.get('request_started_at')
        if (stored_end and am.timestamp(stored_end) != received) or (stored_start and (not am.timestamp(stored_start) or am.instant(stored_start).date() != day or am.instant(stored_start) > received)):
            reject(record, 'stored_request_interval_mismatch'); return None
        if record.get('date'):
            if record['date'] != day.strftime('%Y%m%d'):
                reject(record, 'stored_market_date_mismatch'); return None
            if record.get('tm'):
                try:
                    market_at = datetime.strptime(record['date']+record['tm'], '%Y%m%d%H%M%S').replace(tzinfo=am.KST)
                except (TypeError, ValueError):
                    reject(record, 'stored_market_time_invalid'); return None
                if market_at != observed:
                    reject(record, 'stored_market_time_mismatch'); return None
        return dict(proof, observed_at=observed.isoformat(), received_at=received.isoformat())

    def metric(code, kind, value, unit, proof, *, multiplier=1):
        if kind == 'names':
            try: value = am.label(value)
            except ValueError: return
        else:
            value = am.number(value)
            if value is None or (kind != 'change_pct' and value < 0): return
            value = format(value*Decimal(multiplier), 'f')
        item = dict(scope, value=value, unit=unit, verified=True, basis=proof['evidence_reference'],
                    observed_at=proof['observed_at'], received_at=proof['received_at'])
        if kind == 'turnover': item['period'] = 'intraday_cumulative'
        observations = document['securities'].setdefault(code, {}).setdefault(kind, [])
        if any(o['observed_at'] == item['observed_at'] for o in observations):
            raise ValueError('duplicate_adapter_metric')
        observations.append(item)

    # Rankings first: quote records must bind to an accepted exact parent/code.
    accepted_parents = set()
    for bundle in sources:
        for record in bundle['records']:
            kind = record['kind']
            if kind != 'ranking': continue
            started = am.timestamp(record.get('collected_at'))
            if (record.get('environment') != environment or record.get('test_mode') != int(test_mode)
                    or record.get('status') != 'success' or record.get('api_id') != 'ka00198' or record.get('qry_tp') != '1'
                    or not started or started.date() != day or started > deadline):
                reject(record, 'ranking_scope_status_or_day_mismatch'); continue
            proof = proof_for(record, bundle['sha256'])
            if not proof: continue
            observed = am.instant(proof['observed_at'])
            planned = am.timestamp(record.get('scheduled_at')) if record.get('scheduled_at') else started
            slot = planned.strftime('%H:%M') if planned else None
            slot_ok=(timing.near(window,observed,proof['received_at'],planned)
                     and timing.near(window,started,proof['received_at'],planned)) if window and planned else (
                     observed.strftime('%H:%M')==slot and started.strftime('%H:%M')==slot)
            if (slot not in am.SLOTS or planned.date() != day or not slot_ok or planned>cutoff
                    or started > am.instant(proof['received_at'])):
                reject(record, 'scheduled_and_actual_minute_mismatch'); continue
            stocks = [dict(r, stk_cd=normalize_code(r['stk_cd'])) if candidate_filter else r for r in record['stocks']]
            if record['record_count'] != len(stocks) or len(stocks) != 20:
                reject(record, 'ranking_row_count_mismatch'); continue
            if any(not isinstance(r['stk_cd'], str) or not am.CODE.fullmatch(r['stk_cd']) or r['stk_cd'].endswith(('_NX', '_AL')) for r in stocks):
                reject(record, 'krx_code_scope_unverified'); continue
            document['rankings'].append(dict(scope, api_id='ka00198', qry_tp='1', status='success', slot=slot,
                                             observed_at=proof['observed_at'], received_at=proof['received_at'],
                                             stocks=[dict(stk_cd=r['stk_cd'], rank=r['rank']) for r in stocks]))
            accepted_parents.add(record['id'])
            for r in stocks: metric(r['stk_cd'], 'names', r['stk_nm'], 'text', proof)
            usable_records.append(dict(kind=kind, record_fingerprint=fingerprint(record), source_sha256=bundle['sha256']))
    codes = {r['stk_cd'] for snapshot in document['rankings'] for r in snapshot['stocks']}
    for bundle in sources:
        for record in bundle['records']:
            kind = record['kind']
            if kind == 'ranking': continue
            code = record.get('stock_code') or record.get('requested_code')
            identity = normalize_code if candidate_filter else lambda value: value
            code = identity(code)
            if code not in codes:
                reject(record, 'no_same_day_ranking_code'); continue
            if kind != 'sector' and record.get('environment') != environment:
                reject(record, 'mixed_environment'); continue
            if kind == 'quote' and (record.get('parent_environment') != environment or identity(record.get('parent_code')) != code
                                    or record.get('collection_id') not in accepted_parents
                                    or record.get('batch_collection_id') != record.get('collection_id')
                                    or record.get('batch_api_id') != record.get('api_id')
                                    or record.get('batch_status') not in ('success', 'partial')
                                    or not quote_mode_valid(record,window)):
                reject(record, 'quote_parent_mismatch'); continue
            if kind in ('quote', 'turnover', 'capital') and (record.get('http_status') != 200 or record.get('return_code') != 0):
                reject(record, 'source_request_not_successful'); continue
            if kind in ('quote', 'turnover') and record.get('status') != 'success':
                reject(record, 'source_status_not_successful'); continue
            if kind == 'capital' and record.get('issues'):
                reject(record, 'capital_record_invalid'); continue
            if kind == 'capital' and record.get('api_id') != 'ka10001':
                reject(record, 'capital_api_mismatch'); continue
            if kind == 'quote' and record.get('api_id') not in ('ka10007', 'ka10095'):
                reject(record, 'quote_api_mismatch'); continue
            if kind == 'turnover' and (record.get('api_id') != 'ka10032' or identity(record.get('stored_code')) != code
                                       or record.get('validation_status') != 'syntax_valid'
                                       or record.get('request_profile', {}).get('stex_tp') != '1'):
                reject(record, 'turnover_identity_mismatch'); continue
            if not synthetic and record.get('source_kind') == 'fixture':
                reject(record, 'fixture_not_real_evidence'); continue
            proof = proof_for(record, bundle['sha256'])
            if not proof: continue
            if kind == 'sector':
                updated = am.timestamp(record.get('updated_at'))
                if not updated or updated > am.instant(proof['received_at']):
                    reject(record, 'sector_available_after_evidence'); continue
                document['securities'].setdefault(code, {}).setdefault('sectors', []).append(dict(
                    scope, name=am.label(record['sector_name']), basis=proof['evidence_reference'],
                    observed_at=proof['observed_at'], received_at=proof['received_at']))
            else:
                units = proof.get('units', {})
                if proof.get('unit_runtime_verified') is not True:
                    reject(record, 'runtime_units_unverified'); continue
                if kind == 'capital':
                    if units.get('mac') == 'hundred_million_KRW' and proof.get('market_cap_basis_verified') is True and code == identity(record.get('requested_code')):
                        metric(code, 'market_cap', record.get('mac'), 'KRW', proof, multiplier=100000000)
                    else:
                        reject(record, 'capital_unit_or_basis_unverified'); continue
                else:
                    if units.get('flu_rt') == 'percent': metric(code, 'change_pct', record.get('flu_rt'), 'percent', proof)
                    if units.get('trde_prica') == 'million_KRW' and proof.get('turnover_period') == 'intraday_cumulative':
                        metric(code, 'turnover', record.get('trde_prica'), 'KRW', proof, multiplier=1000000)
                    else: reject(record, 'turnover_unit_or_period_unverified')
                    # Signed prices require independently verified semantics.
                    if units.get('cur_prc') == 'KRW' and proof.get('price_sign_verified') is True:
                        metric(code, 'price', record.get('cur_prc'), 'KRW', proof)
            usable_records.append(dict(kind=kind, record_fingerprint=fingerprint(record), source_sha256=bundle['sha256']))
    document['adapter_diagnostics'] = dict(version='7-C7-v1', source_summaries=[dict(role=s['role'], sha256=s['sha256'],
        hash_unchanged=s['hash_unchanged'], record_count=len(s['records'])) for s in sources],
        unavailable_sources=[s for s, path in [('turnover', turnover_db), ('capital', capital_json)] if path is None],
        rejected=diagnostics, accepted=usable_records, verification='externally_reviewed_manifest_required',
        ranking_test_mode=test_mode, automatic_delivery_enabled=False,
        data_origin='synthetic_fixture' if synthetic else 'unverified')
    if master_db is not None and not candidate_filter:
        from .am_master_adapter import enrich
        enrich(document,master_db,synthetic=synthetic)
    if candidate_filter:
        from .am_candidate_adapter import enrich
        document = enrich(document, master_db, synthetic=synthetic,
                          source_records=[r for s in sources for r in s.get('diagnostic_records', s['records'])])
    # Run the existing engine contract now, so malformed duplicates never escape.
    briefing.analyze(document, generated_at=deadline.isoformat())
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only AM adapter and local dry-run reports; no send mode')
    parser.add_argument('--ranking-db', required=True, type=Path)
    parser.add_argument('--turnover-db', type=Path)
    parser.add_argument('--capital-json', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--master-db', type=Path)
    parser.add_argument('--candidate-filter', action='store_true')
    parser.add_argument('--timing-policy', type=Path)
    parser.add_argument('--date', required=True)
    parser.add_argument('--cutoff', required=True)
    parser.add_argument('--environment', choices=('mock', 'real'), required=True)
    parser.add_argument('--test-mode', action='store_true')
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--name', required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else None
        document = adapt(args.ranking_db, trade_date=args.date, cutoff_at=args.cutoff, environment=args.environment,
                         evidence=manifest, turnover_db=args.turnover_db, capital_json=args.capital_json,
                         test_mode=args.test_mode, synthetic=args.synthetic,master_db=args.master_db,candidate_filter=args.candidate_filter,
                         observation_policy=json.loads(args.timing_policy.read_text(encoding='utf-8')) if args.timing_policy else None)
        report = briefing.analyze(document)
        briefing.write_reports(report, args.name)
        print(json.dumps(dict(status='dry_run', sectors=len(report['sectors']), source_kind=report['source_kind'],
                              missing_slots=report['completeness']['missing_slots'], network_called=False)))
        return 0
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError, sqlite3.Error):
        print(json.dumps(dict(status='rejected', reason='readonly_source_or_evidence_invalid')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
