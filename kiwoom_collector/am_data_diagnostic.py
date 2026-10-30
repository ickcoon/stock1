"""Step 7-C8: local, read-only source diagnostics. No live transport or Store."""
import argparse
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import re
import sqlite3

from . import am_input_adapter as adapter, am_momentum as am, am_briefing as briefing
from . import am_time_policy as timing

SCHEMA = 'am-data-diagnostic-v1'
QUOTE_KEY = ('batch_id', 'collection_id', 'rank', 'requested_code', 'api_id', 'request_id', 'attempt')


def stamp(value):
    instant = am.timestamp(value)
    return instant.isoformat() if instant else None


def code(value):
    return value if isinstance(value, str) and am.CODE.fullmatch(value) else None


def market_time(day, clock):
    if not isinstance(day, str) or not isinstance(clock, str) or not re.fullmatch(r'[0-9]{8}', day) or not re.fullmatch(r'[0-9]{6}', clock):
        return None
    try:
        return datetime.strptime(day+clock, '%Y%m%d%H%M%S').replace(tzinfo=am.KST).isoformat()
    except ValueError:
        return None


def source_error_reason(error):
    """Classify locally; never export an exception message or machine path."""
    if isinstance(error, FileNotFoundError): return 'source_file_not_found'
    if isinstance(error, PermissionError): return 'source_permission_denied'
    if isinstance(error, OSError): return 'source_busy_or_unreadable'
    if isinstance(error, ValueError) and str(error).startswith('Active sidecars:'): return 'active_sidecars_detected'
    if isinstance(error, RuntimeError): return 'source_changed_during_read'
    return 'source_schema_or_data_invalid'


def extras(path, role):
    """Read only additional allowlisted fields missing from the C7 projection."""
    result = dict(quotes={}, runs=[], pages=[])
    with adapter.readonly_database(path) as (db, provenance):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if role == 'ranking' and 'quote_observations' in tables:
            for raw_row in db.execute('SELECT * FROM quote_observations'):
                row = dict(raw_row)
                key = tuple(row.get(k) for k in QUOTE_KEY)
                try:
                    raw = json.loads(row.get('raw_json') or '{}')
                    if not isinstance(raw, dict): raise ValueError()
                    detail = {field: raw.get(field) for field in ('stk_cd', 'dt', 'cntr_tm', 'bid_tm', 'date', 'tm', 'trde_qty', 'cur_prc', 'flu_rt', 'trde_prica')}
                    detail['raw_valid'] = True
                except (ValueError, TypeError):
                    detail = dict(raw_valid=False)
                detail['stored_volume'] = row.get('trde_qty')
                result['quotes'].setdefault(key, []).append(detail)
        if role == 'turnover' and 'turnover_runs' in tables:
            for row in db.execute('SELECT * FROM turnover_runs'):
                item = dict(row)
                result['runs'].append({k: item.get(k) for k in ('id', 'api_id', 'environment', 'source_kind', 'status',
                    'created_at', 'request_started_at', 'request_finished_at', 'finished_at', 'row_count', 'pagination_complete')})
            if 'turnover_pages' in tables:
                for row in db.execute('SELECT run_id,page_no,started_at,finished_at,http_status,return_code,status FROM turnover_pages'):
                    result['pages'].append(dict(row))
    result['sha256'] = provenance['sha256']
    return result


def proof_for(record, source, manifest, day, cutoff, environment, window=None):
    """Return independently declared evidence only if its binding/scope agrees."""
    proof = manifest.get('records', {}).get(adapter.fingerprint(record), {})
    if not isinstance(proof, dict): return None
    observed, received = am.timestamp(proof.get('observed_at')), am.timestamp(proof.get('received_at'))
    if not (proof.get('verified') is True and proof.get('source_sha256') == source['sha256']
            and proof.get('environment') == environment and proof.get('market') == 'KRX'
            and proof.get('trade_date') == day.isoformat() and proof.get('market_verified') is True
            and proof.get('market_time_verified') is True and observed and received
            and timing.available(window,observed,received,cutoff)):
        return None
    end = record.get('request_finished_at') or record.get('response_received_at')
    start = record.get('request_started_at')
    if end and stamp(end) != received.isoformat(): return None
    if start and (not am.timestamp(start) or am.instant(start).date() != day or am.instant(start) > received): return None
    try: am.label(proof.get('evidence_reference'))
    except ValueError: return None
    return proof


def diagnose(ranking_db, *, trade_date, cutoff_at, environment, turnover_db=None, capital_json=None,
             evidence=None, test_mode=False, synthetic=False, observation_policy=None, master_db=None):
    day, cutoff = date.fromisoformat(trade_date), am.instant(cutoff_at)
    window=timing.from_document({'observation_policy':observation_policy})
    deadline=window.deadline(cutoff) if window else cutoff
    if cutoff.date() != day or not '09:20' <= cutoff.strftime('%H:%M') <= '09:50' or environment not in ('mock', 'real'):
        raise ValueError('invalid_diagnostic_scope')
    if type(synthetic) is not bool or type(test_mode) is not bool: raise ValueError('invalid_diagnostic_mode')
    manifest = evidence or {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get('records', {}), dict):
        raise ValueError('invalid_manifest_scope')
    if manifest and (manifest.get('schema_version') != 'am-verification-manifest-v1' or manifest.get('synthetic') is not synthetic):
        raise ValueError('invalid_manifest_scope')
    sources, additional, missing = {}, {}, []
    for role, path in [('ranking', ranking_db), ('turnover', turnover_db), ('capital', capital_json)]:
        if path is None:
            missing.append(dict(section=role, reason='source_not_provided')); continue
        try:
            bundle = adapter.read_capital(path) if role == 'capital' else adapter.read_sources(path, role=role)
            extra = extras(path, role) if role != 'capital' else {}
            if extra and extra['sha256'] != bundle['sha256']: raise ValueError('changed')
            sources[role], additional[role] = bundle, extra
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, RuntimeError) as error:
            missing.append(dict(section=role, reason=source_error_reason(error)))
    rankings = sources.get('ranking', {}).get('records', [])
    selected = [r for r in rankings if r['kind'] == 'ranking' and r.get('environment') == environment
                and r.get('test_mode') == int(test_mode) and r.get('api_id') == 'ka00198' and r.get('qry_tp') == '1'
                and am.timestamp(r.get('collected_at')) and am.instant(r['collected_at']).date() == day]
    slots, target_codes = [], set()
    expected_slots=[s for s in am.SLOTS if not window or am.instant(trade_date+'T'+s+':00+09:00')<=cutoff]
    for slot in expected_slots:
        attempts = [r for r in selected if (stamp(r.get('scheduled_at')) or stamp(r.get('collected_at')))[11:16] == slot]
        successful = [r for r in attempts if r.get('status') == 'success']
        row = dict(slot=slot, attempt_count=len(attempts), success_count=len(successful), stored_top20_complete=False,
                   verified_top20_complete=False, collection_started_at=None, scheduled_at=None,
                   observed_at=None, response_received_at=None, missing_evidence=[])
        if len(successful) != 1:
            row['missing_evidence'].append('successful_slot_missing' if not successful else 'duplicate_successful_slot')
        else:
            record = successful[0]; stocks = record['stocks']
            codes = [code(s.get('stk_cd')) for s in stocks]
            complete = (record.get('record_count') == len(stocks) == 20 and None not in codes and len(set(codes)) == 20
                        and all(type(s['rank']) is int for s in stocks)
                        and all(not c.endswith(('_NX','_AL')) for c in codes)
                        and sorted(s['rank'] for s in stocks) == list(range(1,21)))
            target_codes.update(c for c in codes if c)
            row.update(collection_started_at=stamp(record['collected_at']), scheduled_at=stamp(record.get('scheduled_at')),
                       stored_top20_complete=complete)
            if not complete: row['missing_evidence'].append('top20_incomplete_or_duplicate')
            proof = proof_for(record, sources['ranking'], manifest, day, cutoff, environment,window)
            anchor=am.instant(trade_date+'T'+slot+':00+09:00')
            slot_ok=(proof and timing.near(window,proof['observed_at'],proof['received_at'],anchor)
                     and timing.near(window,record['collected_at'],proof['received_at'],anchor))
            if slot_ok:
                row.update(observed_at=stamp(proof['observed_at']), response_received_at=stamp(proof['received_at']),
                           verified_top20_complete=complete)
            else: row['missing_evidence'].append('ranking_observation_or_receipt_unverified')
        slots.append(row)

    quotes = []
    morning_parents={r['id'] for r in selected if (stamp(r.get('scheduled_at')) or stamp(r.get('collected_at')))[11:16] in expected_slots}
    for record in rankings:
        if record['kind'] != 'quote' or record.get('environment') != environment or record.get('parent_test_mode') != int(test_mode): continue
        if window and record.get('collection_id') not in morning_parents: continue
        started = am.timestamp(record.get('request_started_at'))
        if not started or started.date() != day: continue
        stock = code(record.get('requested_code'))
        if stock not in target_codes: continue
        issues = []
        details = additional['ranking']['quotes'].get(tuple(record.get(k) for k in QUOTE_KEY), [])
        detail = details[0] if len(details) == 1 else {}
        if len(details) != 1: issues.append('quote_row_ambiguous')
        api = record.get('api_id') if record.get('api_id') in ('ka10007','ka10095') else None
        raw_at = market_time(detail.get('dt'), detail.get('cntr_tm')) if api == 'ka10095' else market_time(detail.get('date') or record.get('date'), detail.get('tm') or record.get('tm'))
        if not raw_at: issues.append('per_stock_market_time_missing')
        elif not timing.available(window,raw_at,raw_at,cutoff): issues.append('market_time_wrong_day_or_after_cutoff')
        if window and raw_at:
            parent=next((r for r in selected if r['id']==record.get('collection_id')),None)
            anchor=(parent.get('scheduled_at') or parent['collected_at']) if parent else None
            if not anchor or not timing.near(window,raw_at,record.get('request_finished_at'),anchor):
                issues.append('outside_observation_window')
        if detail.get('stk_cd') is not None and detail.get('stk_cd') != stock: issues.append('raw_stock_code_mismatch')
        if stock.endswith(('_NX','_AL')): issues.append('code_market_scope_mismatch')
        if record.get('date') or record.get('tm'):
            stored_at=market_time(record.get('date'),record.get('tm'))
            if not stored_at or (raw_at and stored_at != raw_at): issues.append('stored_and_raw_market_time_disagree')
        if any(detail.get(field) is not None and am.number(detail[field]) != am.number(record.get(field))
               for field in ('cur_prc','flu_rt','trde_prica')):
            issues.append('stored_and_raw_market_values_disagree')
        if (record.get('parent_code') != stock or record.get('parent_environment') != environment
                or record.get('batch_collection_id') != record.get('collection_id') or record.get('batch_api_id') != api
                or record.get('batch_status') not in ('success','partial') or not adapter.quote_mode_valid(record,window)):
            issues.append('quote_parent_or_batch_mismatch')
        if record.get('status') != 'success' or record.get('http_status') != 200 or record.get('return_code') != 0:
            issues.append('quote_request_not_successful')
        proof = proof_for(record, sources['ranking'], manifest, day, cutoff, environment,window)
        if not proof: issues.append('quote_observation_or_receipt_unverified')
        elif raw_at != stamp(proof['observed_at']): issues.append('market_time_does_not_match_review')
        units = proof.get('units', {}) if proof else {}
        if not isinstance(units, dict): units = {}
        verified_units = bool(proof and proof.get('unit_runtime_verified') is True)
        values = {}
        for field, unit, value in [('cur_prc','KRW',record.get('cur_prc')), ('flu_rt','percent',record.get('flu_rt')),
                                    ('trde_qty','shares',detail.get('trde_qty') if detail.get('trde_qty') is not None else detail.get('stored_volume')),
                                    ('trde_prica','million_KRW',record.get('trde_prica'))]:
            number = am.number(value)
            valid = number is not None
            if valid and field == 'cur_prc': valid = number > 0
            elif valid and field != 'flu_rt': valid = number >= 0
            if valid and field == 'trde_qty' and number != number.to_integral_value(): valid=False
            if not valid: issues.append(field+'_missing_or_invalid')
            unit_ok = verified_units and units.get(field) == unit
            if not unit_ok: issues.append(field+'_unit_unverified')
            values[field] = dict(value=format(number,'f') if valid else None, declared_unit=unit, unit_verified=unit_ok)
        if not proof or proof.get('price_sign_verified') is not True: issues.append('price_sign_unverified')
        if not proof or proof.get('turnover_period') != 'intraday_cumulative': issues.append('turnover_period_unverified')
        quotes.append(dict(stock_code=stock, api_id=api, record_fingerprint=adapter.fingerprint(record),
                           request_started_at=stamp(record.get('request_started_at')), local_request_finished_at=stamp(record.get('request_finished_at')),
                           raw_market_observed_at=raw_at, market_time_kind='execution_time' if api=='ka10095' else 'api_time',
                           response_received_at=stamp(proof.get('received_at')) if proof else None, values=values,
                           missing_evidence=sorted(set(issues)), verified=not issues))

    runs = []
    for run in additional.get('turnover', {}).get('runs', []):
        at = am.timestamp(run.get('request_started_at') or run.get('created_at'))
        if run.get('environment') != environment or not at or at.date() != day: continue
        pages=[]
        for page in additional['turnover']['pages']:
            if page['run_id'] != run['id']: continue
            records=[r for r in sources['turnover']['records'] if r['run_id']==run['id'] and r['page_no']==page['page_no']]
            proofs=[proof_for(r,sources['turnover'],manifest,day,cutoff,environment,window) for r in records]
            received=stamp(page.get('finished_at')) if records and all(proofs) else None
            issues=[] if received else ['run_response_receipt_unverified']
            if page.get('status')!='success' or page.get('http_status')!=200 or page.get('return_code')!=0:
                issues.append('turnover_page_not_successful')
            pages.append(dict(page_no=page['page_no'] if type(page['page_no']) is int else None,
                              request_started_at=stamp(page['started_at']),local_request_finished_at=stamp(page['finished_at']),
                              response_received_at=received,missing_evidence=issues))
        runs.append(dict(run_fingerprint=adapter.fingerprint(run), api_id='ka10032' if run.get('api_id')=='ka10032' else None,
                         source_kind=run.get('source_kind') if run.get('source_kind') in ('fixture','live_test') else 'unknown',
                         status=run.get('status') if run.get('status') in ('success','partial','empty','error','running','interrupted') else 'unknown',
                         request_started_at=stamp(run.get('request_started_at')), local_request_finished_at=stamp(run.get('request_finished_at')),
                         declared_row_count=run.get('row_count') if type(run.get('row_count')) is int else None,
                         pagination_complete=run.get('pagination_complete') == 1, pages=pages))
    if not runs: missing.append(dict(section='turnover', reason='same_day_collection_run_unconfirmed'))
    if not any(q['api_id']=='ka10095' for q in quotes): missing.append(dict(section='quotes', reason='same_day_ka10095_rows_missing'))
    for stock in sorted(target_codes):
        if not any(q['stock_code']==stock for q in quotes): missing.append(dict(section='quotes', stock_code=stock, reason='same_day_quote_missing'))

    report = None; adapter_state = 'unavailable'
    if 'ranking' in sources:
        try:
            document = adapter.adapt(ranking_db, trade_date=trade_date, cutoff_at=cutoff_at, environment=environment,
                evidence=manifest, turnover_db=turnover_db if 'turnover' in sources else None,
                capital_json=capital_json if 'capital' in sources else None, test_mode=test_mode, synthetic=synthetic,
                observation_policy=observation_policy,master_db=master_db)
            expected_hashes = {r: s['sha256'] for r,s in sources.items()}
            actual_hashes = {s['role']: s['sha256'] for s in document['adapter_diagnostics']['source_summaries']}
            if expected_hashes != actual_hashes: raise ValueError('source_changed')
            report = briefing.analyze(document, generated_at=deadline.isoformat()); adapter_state = 'validated'
            rejected = Counter(d['reason'] for d in document['adapter_diagnostics']['rejected'])
            missing.extend(dict(section='adapter', reason=reason, count=count) for reason,count in sorted(rejected.items()))
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, ArithmeticError, AttributeError, RuntimeError):
            adapter_state = 'rejected'; missing.append(dict(section='adapter', reason='source_changed_or_adapter_contract_rejected'))
    candidates = []
    for candidate in report['candidates'] if report else []:
        metrics = candidate['metrics']
        current_quotes = [q for q in quotes if q['stock_code']==candidate['stock_code'] and q['raw_market_observed_at']
                          and timing.near(window,q['raw_market_observed_at'],q['response_received_at'],cutoff)]
        candidates.append(dict(stock_code=candidate['stock_code'], engine_status=candidate['status'],
            price_connected=any(q['verified'] for q in current_quotes), turnover_connected=metrics['turnover'] is not None,
            capital_connected=metrics['market_cap'] is not None, sector_connected=candidate['sector'] is not None,
            missing_evidence=candidate['missing_evidence'] + ([] if any(q['verified'] for q in current_quotes) else ['cutoff_quote_diagnostics_not_verified'])))
    chosen = {c['stock_code'] for s in report['sectors'] for c in s['leaders']} if report else set()
    ready = bool(chosen and all(not c['missing_evidence'] for c in candidates if c['stock_code'] in chosen)
                 and all(s['verified_top20_complete'] for s in slots))
    result = dict(schema_version=SCHEMA, trade_date=trade_date, selection_cutoff_at=cutoff.isoformat(), environment=environment,
        market='KRX', source_kind='fixture' if synthetic else 'readonly_diagnostic',
        network_called=False, operational_writes=False, candidate_confirmed=False,
        sources={role:dict(sha256=s['sha256'],hash_unchanged=s['hash_unchanged'],record_count=len(s['records'])) for role,s in sources.items()},
        ranking_slots=slots, quotes=quotes, turnover_runs=runs,
        capital=dict(source_present='capital' in sources, stored_records=len(sources.get('capital',{}).get('records',[])),
                     connected_stock_count=sum(c['capital_connected'] for c in candidates)),
        sectors=dict(stored_assignments=sum(r['kind']=='sector' for r in rankings),
                     connected_stock_count=sum(c['sector_connected'] for c in candidates)),
        am_selection=dict(adapter_state=adapter_state, engine_sector_count=len(report['sectors']) if report else 0,
                          ready_for_verified_briefing=ready, candidates=candidates), missing_evidence=missing)
    result['summary'] = summarize(result)
    if window:
        result.update(observation_policy=window.document(),decision_deadline_at=deadline.isoformat())
    if master_db is not None and report:
        result['master_diagnostics']=document.get('master_diagnostics',{})
        result['capital']['source_present']=True
    result['evaluation_reference'] = adapter.fingerprint(result)
    return result


def summarize(report):
    stored = sum(s['stored_top20_complete'] for s in report['ranking_slots'])
    verified = sum(s['verified_top20_complete'] for s in report['ranking_slots'])
    lines = ['AM 실데이터 진단. '+report['trade_date']+' '+report['environment']+' KRX.',
             '합성 테스트 데이터입니다.' if report['source_kind']=='fixture' else '읽기 전용 진단입니다. 시장 검증 완료를 의미하지 않습니다.',
             f"0198 저장 TOP20 완전 슬롯 {stored}/{len(report['ranking_slots'])}. 시각까지 검증된 슬롯 {verified}/{len(report['ranking_slots'])}.",
             f"시세 {len(report['quotes'])}개. 검증 통과 {sum(q['verified'] for q in report['quotes'])}개.",
             f"0186 당일 수집 회차 {len(report['turnover_runs'])}개. 시총 연결 {report['capital']['connected_stock_count']}종목.",
             f"섹터 연결 {report['sectors']['connected_stock_count']}종목. 엔진 후보 섹터 {report['am_selection']['engine_sector_count']}개.",
             'AM 브리핑 선정 검증: '+('가능' if report['am_selection']['ready_for_verified_briefing'] else '보류')+'.']
    reasons = Counter(m['reason'] for m in report['missing_evidence'])
    reasons.update(r for s in report['ranking_slots'] for r in s['missing_evidence'])
    reasons.update(r for q in report['quotes'] for r in q['missing_evidence'])
    reasons.update(r for run in report['turnover_runs'] for page in run['pages'] for r in page['missing_evidence'])
    reasons.update(r for c in report['am_selection']['candidates'] for r in c['missing_evidence'])
    readable=Counter()
    for reason,count in reasons.items(): readable[reason_label(reason)]+=count
    lines += ['부족 근거:'] + [f'- {label}: {count}건' for label,count in sorted(readable.items())] if readable else ['부족 근거 없음.']
    lines.append('API 호출·전송·운영 DB 변경 없음.')
    return '\n'.join(lines)


def reason_label(reason):
    if reason=='outside_observation_window': return '설정한 오전 관측 허용 범위 밖임'
    labels = dict(source_not_provided='소스 미제공', source_unavailable_busy_or_schema_invalid='소스 접근 불가 또는 저장 구조 불일치',
        successful_slot_missing='성공 관측 없음', duplicate_successful_slot='성공 관측 중복',
        top20_incomplete_or_duplicate='TOP20 부족 또는 중복', ranking_observation_or_receipt_unverified='관심도 관측·수신 시각 미검증',
        per_stock_market_time_missing='종목별 시장 시각 없음', market_time_wrong_day_or_after_cutoff='시장 시각의 거래일·기준 시각 불일치',
        raw_stock_code_mismatch='원문 종목코드 불일치', quote_parent_or_batch_mismatch='시세 원본·배치 연결 불일치',
        quote_request_not_successful='시세 요청 실패 또는 미완료', quote_observation_or_receipt_unverified='시세 관측·수신 시각 미검증',
        market_time_does_not_match_review='원문 시각과 검토 근거 불일치', price_sign_unverified='가격 부호 미검증',
        turnover_period_unverified='오전 누적 거래대금 범위 미검증', same_day_collection_run_unconfirmed='0186 당일 수집 회차 미확인',
        same_day_ka10095_rows_missing='ka10095 당일 종목 자료 없음', same_day_quote_missing='당일 시세 없음',
        source_changed_or_adapter_contract_rejected='소스 변경 또는 입력 검증 실패', bound_verification_missing='원본에 연결된 검증 근거 없음',
        no_same_day_ranking_code='같은 거래일 관심도 종목 연결 불가', runtime_units_unverified='실측 단위 미검증',
        verification_scope_mismatch='검증 근거의 거래일·시장·환경 불일치', verification_time_outside_cutoff='검증 시각이 기준 범위 밖임',
        ranking_scope_status_or_day_mismatch='관심도 거래일·환경·상태 불일치', quote_row_ambiguous='시세 행 중복 또는 식별 불가')
    labels.update(run_response_receipt_unverified='0186 페이지 응답 수신 시각 미검증',
                  stored_and_raw_market_values_disagree='원문과 저장 시세 값 불일치',
                  stored_and_raw_market_time_disagree='원문과 저장 시각 불일치', code_market_scope_mismatch='종목코드의 거래소 범위 불일치',
                  source_file_not_found='소스 파일 없음', source_permission_denied='소스 읽기 권한 없음',
                  source_busy_or_unreadable='소스 사용 중 또는 읽기 불가', active_sidecars_detected='활성 WAL·저널로 읽기 보류',
                  source_changed_during_read='읽는 동안 원본 변경 감지', source_schema_or_data_invalid='소스 저장 구조 또는 데이터 형식 오류',
                  turnover_page_not_successful='0186 페이지 요청 실패 또는 미완료',
                  market_cap_unverified='시총 미검증', sector_missing_or_ambiguous='섹터 누락 또는 복수 분류',
                  intraday_turnover_unverified='오전 누적 거래대금 미검증', change_pct_unverified='등락률 미검증',
                  cutoff_quote_diagnostics_not_verified='기준 시점 시세 진단 미통과', incomplete_observations='관심도 관측 불완전',
                  repeated_attention_insufficient='반복 관심도 근거 부족')
    if reason in labels: return labels[reason]
    for field,name in [('cur_prc','가격'),('flu_rt','등락률'),('trde_qty','거래량'),('trde_prica','거래대금')]:
        if reason == field+'_unit_unverified': return name+' 단위 미검증'
        if reason == field+'_missing_or_invalid': return name+' 누락 또는 형식 오류'
    return '입력 연결·시점 근거 부족'


def write_reports(report, name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name): raise ValueError('invalid_name')
    root = am.REPORTS.resolve()
    if root != am.PROJECT.resolve()/'reports': raise ValueError('invalid_output_root')
    root.mkdir(exist_ok=True)
    contents = {'.diagnostic.json':json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False), '.summary.txt':report['summary']}
    paths = [root/(name+s) for s in contents]
    if any(p.exists() or p.is_symlink() for p in paths): raise ValueError('existing_report_preserved')
    created=[]
    try:
        for path,content in zip(paths,contents.values()):
            with path.open('x',encoding='utf-8',newline='\n') as stream:
                created.append(path);stream.write(content)
    except BaseException:
        for path in created: path.unlink()
        raise
    return paths


def main(argv=None):
    parser=argparse.ArgumentParser(description='Read-only hospital AM diagnostics; no send or network mode')
    parser.add_argument('--ranking-db',required=True,type=Path)
    parser.add_argument('--turnover-db',type=Path);parser.add_argument('--capital-json',type=Path)
    parser.add_argument('--evidence',type=Path);parser.add_argument('--date',required=True)
    parser.add_argument('--master-db',type=Path);parser.add_argument('--timing-policy',type=Path)
    parser.add_argument('--cutoff',required=True);parser.add_argument('--environment',required=True,choices=('mock','real'))
    parser.add_argument('--test-mode',action='store_true');parser.add_argument('--synthetic',action='store_true')
    parser.add_argument('--name',required=True);parser.add_argument('--format',choices=('summary','json'),default='summary')
    args=parser.parse_args(argv)
    try:
        evidence=json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else None
        report=diagnose(args.ranking_db,trade_date=args.date,cutoff_at=args.cutoff,environment=args.environment,
            turnover_db=args.turnover_db,capital_json=args.capital_json,evidence=evidence,test_mode=args.test_mode,synthetic=args.synthetic,
            master_db=args.master_db,observation_policy=json.loads(args.timing_policy.read_text(encoding='utf-8')) if args.timing_policy else None)
        write_reports(report,args.name)
        print(json.dumps(report,ensure_ascii=True) if args.format=='json' else report['summary'])
        return 0 if report['am_selection']['ready_for_verified_briefing'] else 2
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,ArithmeticError,AttributeError,RuntimeError):
        print(json.dumps(dict(status='rejected',reason='diagnostic_scope_or_output_invalid')))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
