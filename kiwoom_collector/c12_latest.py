"""C12-AG offline snapshot selection. No Store, credentials, network or delivery.

Collection clocks are explicitly not exchange market clocks. Historical 0198
completion can be bounded by the success log written AFTER Store.finish commits.
Neither collected_at nor a DB file mtime proves completion.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from .am_time_policy import instant, KST


@dataclass(frozen=True)
class Policy:
    ranking_ttl_seconds: int = 360
    turnover_ttl_seconds: int = 900

    def __post_init__(self):
        if any(type(v) is not int or not 0 < v <= 86400 for v in asdict(self).values()):
            raise ValueError('invalid_snapshot_ttl')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@contextmanager
def read_db(path):
    """WAL-aware short read transaction; never immutable on a live database."""
    db = sqlite3.connect(Path(path).resolve(strict=True).as_uri()+'?mode=ro', uri=True, timeout=.2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        started = time.monotonic()
        db.set_progress_handler(lambda: int(time.monotonic()-started > 2), 1000)
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def log_completions(path):
    """Allowlist only UUID, post-commit success timestamp and count, not log text."""
    result = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        match = re.match(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) INFO .*?([0-9a-f-]{36}): (\d+).*\(success\)$', line)
        if match:
            stamp, identifier, count = match.groups()
            # Log precision truncates submilliseconds: use the END of that ms.
            at = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S,%f').replace(tzinfo=KST)
            from datetime import timedelta
            at += timedelta(milliseconds=1)
            if identifier in result:
                raise ValueError('ambiguous_completion_log')
            result[identifier] = dict(db_committed_by=at.isoformat(), record_count=int(count),
                                      completion_basis='post_commit_success_log')
    return result


def load(ranking_db, turnover_dbs=(), *, day, completion_log=None, ledgers=()):
    """Read bounded day records; ledger mtime is only a conservative upper bound.

    A ledger must bind the exact run ID and counts. A copied/modified ledger
    can only make this bound later; it cannot authorize historical availability.
    """
    completions = log_completions(completion_log) if completion_log else {}
    result = []
    with read_db(ranking_db) as db:
        runs = [dict(r) for r in db.execute(
            "SELECT * FROM collections WHERE collected_at>=? AND collected_at<? LIMIT 1001",
            (day, day+'Z'))]
        if len(runs) > 1000: raise ValueError('day_read_limit')
        for run in runs:
            rows = [dict(r) for r in db.execute('SELECT rank,stk_cd,stk_nm FROM ranking_records WHERE collection_id=? ORDER BY rank', (run['id'],))]
            evidence = completions.get(run['id'], {})
            count_ok = evidence.get('record_count') == run['record_count'] == len(rows)
            result.append(dict(api='0198', id=run['id'], environment=run['environment'],
                status=run['status'], test_mode=run['test_mode'], api_id=run['api_id'], qry_tp=run['qry_tp'],
                collection_started_at=run['collected_at'], request_started_at=None,
                response_received_at=None, db_committed_by=evidence.get('db_committed_by') if count_ok else None,
                completion_basis=evidence.get('completion_basis'), market_asof=None,
                market_time_status='not_provided', rows=rows, scheduled_at=run.get('scheduled_at')))
    bounds = {}
    for path in ledgers:
        path = Path(path); ledger = json.loads(path.read_text(encoding='utf-8'))
        turn = ledger.get('turnover', {})
        if ledger.get('environment') == 'real' and turn.get('environment') == 'real' and turn.get('run_id'):
            bounds[turn['run_id']] = (datetime.fromtimestamp(path.stat().st_mtime, KST).isoformat(), turn.get('rows'))
    for path in turnover_dbs:
        with read_db(path) as db:
            runs = [dict(r) for r in db.execute('SELECT * FROM turnover_runs WHERE created_at>=? AND created_at<? LIMIT 1001', (day, day+'Z'))]
            if len(runs)>1000: raise ValueError('day_read_limit')
            for run in runs:
                rows = [dict(r) for r in db.execute('SELECT stock_code,turnover_rank,validation_status,raw_json FROM turnover_rows WHERE run_id=? ORDER BY page_no,row_no', (run['id'],))]
                pages = [dict(r) for r in db.execute('SELECT http_status,return_code,status,row_count FROM turnover_pages WHERE run_id=?', (run['id'],))]
                bound, count = bounds.get(run['id'], (None,None))
                good = (run['status']=='success' or (run['status']=='partial' and json.loads(run['issues_json'])==['page_limit_reached']))
                good = good and bool(pages) and all(p['http_status']==200 and p['return_code']==0 and p['status']=='success' for p in pages)
                good = good and sum(p['row_count'] for p in pages)==run['row_count']==len(rows)
                result.append(dict(api='0186', id=run['id'], environment=run['environment'],
                    status='success' if good else 'invalid', original_status=run['status'],
                    api_id=run['api_id'], source_kind=run['source_kind'], profile=json.loads(run['request_json']),
                    collection_started_at=run['created_at'], request_started_at=run['request_started_at'],
                    response_received_at=run['request_finished_at'], db_finished_at=run['finished_at'],
                    db_committed_by=bound if count==len(rows) else None,
                    completion_basis='post_commit_ledger_mtime_upper_bound', market_asof=None,
                    market_time_status='not_provided', rows=rows))
    for item in result: item['snapshot_sha256'] = digest(item)
    return result


def select(snapshots, *, as_of, environment, policy=Policy()):
    at = instant(as_of)
    if environment not in ('mock','real'): raise ValueError('explicit_environment_required')
    output = dict(as_of=at.isoformat(), environment=environment, policy=asdict(policy), selected={}, rejected=[])
    for api, ttl in [('0198',policy.ranking_ttl_seconds),('0186',policy.turnover_ttl_seconds)]:
        valid = []
        for s in snapshots:
            if s['api'] != api: continue
            reason = None
            try:
                start, end = instant(s['collection_started_at']), instant(s['db_committed_by'])
                request = instant(s['request_started_at']) if s.get('request_started_at') else start
                response = instant(s['response_received_at']) if s.get('response_received_at') else None
                if s.get('snapshot_sha256') != digest({k:v for k,v in s.items() if k!='snapshot_sha256'}): reason='snapshot_fingerprint_mismatch'
                elif s['environment'] != environment: reason='mixed_environment'
                elif s['status']!='success': reason='not_successful'
                elif not (start.date()==end.date()==at.date() and start<=request<=end<=at): reason='unfinished_future_or_wrong_day'
                elif response and not request<=response<=end: reason='invalid_response_interval'
                elif s.get('db_finished_at') and not request<=instant(s['db_finished_at'])<=end: reason='invalid_storage_interval'
                elif api=='0198' and (s.get('api_id')!='ka00198' or s.get('qry_tp')!='1' or s.get('test_mode')!=0): reason='ranking_scope_invalid'
                elif api=='0186' and (s.get('api_id')!='ka10032' or s.get('source_kind')!='live_test' or s.get('profile')!={'mrkt_tp':'000','mang_stk_incls':'0','stex_tp':'1'}): reason='turnover_scope_invalid'
                elif not s['rows']: reason='empty_snapshot'
                elif api=='0198' and (len(s['rows'])!=20 or {r['rank'] for r in s['rows']}!=set(range(1,21))): reason='incomplete_top20'
                codes=[r['stk_cd'] if api=='0198' else r['stock_code'] for r in s['rows']]
                if not reason and (len(set(codes))!=len(codes) or any(not isinstance(c,str) or not re.fullmatch('[0-9A-Z]{6}',c) for c in codes)): reason='ambiguous_or_invalid_codes'
                # Response time is the age anchor. If absent, request/collection
                # start is conservative; never rejuvenate old data by late storage.
                age=(at-(response or request)).total_seconds()
                if not reason: valid.append((end,s,age))
            except (ValueError,TypeError,KeyError): reason='collection_completion_unknown'
            if reason: output['rejected'].append(dict(api=api,id=s['id'],reason=reason))
        valid.sort(key=lambda v:(v[0],v[1]['id']))
        if len(valid)>1 and valid[-1][0]==valid[-2][0]:
            output['selected'][api]=dict(status='ambiguous',snapshot=None)
        elif valid:
            _,s,age=valid[-1]
            output['selected'][api]=dict(status='fresh' if age<=ttl else 'stale', age_seconds=age, snapshot=s)
        else: output['selected'][api]=dict(status='missing',snapshot=None)
    return output


def observe(selection, history, securities):
    """Manual-trading observations, independent of confirmed candidate gates.

    Sector order is an attention/coverage observation, not the existing C12
    candidate score. Unknown classifications remain explicit and ungrouped.
    """
    ranking=selection['selected']['0198']['snapshot']
    turnover=selection['selected']['0186']['snapshot']
    stocks={}
    histories={r['stock_code']:r for r in history}
    for r in (ranking or {}).get('rows',[]):
        stocks[r['stk_cd']]=dict(stock_code=r['stk_cd'],name=r.get('stk_nm'),ranking_rank=r['rank'])
    for r in (turnover or {}).get('rows',[]):
        rank=r.get('turnover_rank')
        if type(rank) is not int or not 1<=rank<=40:continue
        code=r['stock_code'];raw=json.loads(r['raw_json'])
        if raw.get('stk_cd')!=code:continue
        stock=stocks.setdefault(code,dict(stock_code=code,name=raw.get('stk_nm'),ranking_rank=None))
        stock.update(turnover_rank=rank,
            raw_turnover=dict(value=raw.get('trde_prica'),unit=None,verification='unverified',confirmed_amount_won=None),
            raw_change=raw.get('flu_rt'))
    groups={};unknown=[]
    for code,s in stocks.items():
        s['top5_appearances']=histories.get(code,{}).get('top5_appearances',0)
        s['candidate_confirmed']=False
        tags=sorted({r['name'] for r in securities.get(code,{}).get('sectors',[])})
        s['sector_status']='known' if len(tags)==1 else 'missing_or_ambiguous'
        s['sector']=tags[0] if len(tags)==1 else None
        if s['sector']:groups.setdefault(s['sector'],[]).append(s)
        else:unknown.append(code)
    sectors=[]
    for name,members in groups.items():
        sectors.append(dict(name=name,stock_codes=sorted(s['stock_code'] for s in members),
            attention_count=sum(s.get('ranking_rank') is not None for s in members),
            current_top5_count=sum(s.get('ranking_rank') is not None and s['ranking_rank']<=5 for s in members),
            turnover_top40_count=sum(s.get('turnover_rank') is not None for s in members),
            candidate_confirmed=False))
    sectors.sort(key=lambda s:(-s['current_top5_count'],-s['attention_count'],-s['turnover_top40_count'],s['name']))
    for i,s in enumerate(sectors,1):s['observation_rank']=i
    watchlist=sorted(stocks.values(),key=lambda s:(s.get('ranking_rank') is None,s.get('ranking_rank') or 999,s.get('turnover_rank') or 999,s['stock_code']))
    return dict(status='available' if watchlist else 'unavailable',watchlist=watchlist,sectors=sectors,
        sector_order_basis='current_top5_count_then_attention_count_then_turnover_top40_count',
        unknown_sector_stock_codes=sorted(unknown),turnover_scope='first_page_top40',
        candidate_policy_applied=False,candidate_confirmed=False,
        data_warnings=[dict(api=api,status=v['status'],age_seconds=v.get('age_seconds'))
                       for api,v in selection['selected'].items() if v['status']!='fresh'])


def analyze(snapshots, *, as_of, environment, policy=Policy(), generated_at=None, master_db=None, unit_evidence=None):
    """Run existing C12 policy; unverified units/capital/sectors stay held.

    Time provenance now permits ranking analysis without inventing exchange
    timestamps. Collection-time observations carry an explicit time_basis.
    """
    from . import am_momentum as am, am_briefing as briefing
    from .am_candidate_adapter import enrich
    selection=select(snapshots,as_of=as_of,environment=environment,policy=policy)
    at=instant(as_of); generated=instant(generated_at or datetime.now(KST))
    if generated<at: raise ValueError('analysis_before_as_of')
    scope=dict(source_kind='readonly_adapter',environment=environment,market='KRX')
    doc=dict(schema_version=am.ADAPTER_INPUT_SCHEMA,**scope,trade_date=at.date().isoformat(),
        selection_cutoff_at=at.isoformat(),rankings=[],securities={},latest_selection=selection)
    from .am_time_policy import ObservationPolicy
    doc['observation_policy']=ObservationPolicy(receipt_grace_seconds=0).document()
    ranking=selection['selected']['0198']
    current=ranking['snapshot']
    if current:
        # Keep historical standard slots for the existing repeated-attention and
        # completeness rules; admit the latest independent 5-minute slot too.
        latest_start=instant(current['collection_started_at'])
        for s in snapshots:
            if s['api']!='0198':continue
            one=select([s],as_of=as_of,environment=environment,policy=policy)['selected']['0198']
            if not one['snapshot']:continue
            start=instant(s['collection_started_at']);slot=start.strftime('%H:%M')
            if start>latest_start or (slot not in am.SLOTS and s['id']!=current['id']):continue
            doc['rankings'].append(dict(scope,api_id='ka00198',qry_tp='1',status='success',slot=slot,
                observed_at=start.isoformat(),received_at=s['db_committed_by'],
                time_basis='collection_clock_not_market_clock',stocks=[dict(stk_cd=r['stk_cd'],rank=r['rank']) for r in s['rows']]))
        for r in current['rows']:
            try: name=am.label(r['stk_nm'])
            except ValueError: continue
            doc['securities'].setdefault(r['stk_cd'],{})['names']=[dict(scope,value=name,unit='text',verified=True,
                basis='stored_ranking_name',observed_at=current['collection_started_at'],received_at=current['db_committed_by'],
                snapshot_sha256=current['snapshot_sha256'],time_basis='collection_clock_not_market_clock')]
    turnover=selection['selected']['0186']['snapshot']
    proof=(unit_evidence or {}).get('snapshots',{}).get((turnover or {}).get('snapshot_sha256'),{})
    if turnover and proof.get('environment')==environment and proof.get('unit_runtime_verified') is True:
        # Preserve existing runtime-unit, accumulation-basis and signed-price
        # requirements. Collection provenance never proves units or semantics.
        basis=am.label(proof.get('evidence_reference'))
        for r in turnover['rows']:
            if r.get('validation_status')!='syntax_valid':continue
            raw=json.loads(r['raw_json']);code=r['stock_code']
            if raw.get('stk_cd')!=code:continue
            for kind,field,unit,multiplier,allowed in [
                ('change_pct','flu_rt','percent',1,proof.get('units',{}).get('flu_rt')=='percent'),
                ('turnover','trde_prica','KRW',1000000,proof.get('units',{}).get('trde_prica')=='million_KRW' and proof.get('turnover_period')=='intraday_cumulative'),
                ('price','cur_prc','KRW',1,proof.get('units',{}).get('cur_prc')=='KRW' and proof.get('price_sign_verified') is True)]:
                value=am.number(raw.get(field))
                if not allowed or value is None or (kind!='change_pct' and value<0):continue
                item=dict(scope,value=str(value*multiplier),unit=unit,verified=True,basis=basis,
                    observed_at=turnover['request_started_at'],received_at=turnover['response_received_at'],
                    snapshot_sha256=turnover['snapshot_sha256'],time_basis='collection_clock_not_market_clock')
                if kind=='turnover':item['period']='intraday_cumulative'
                doc['securities'].setdefault(code,{}).setdefault(kind,[]).append(item)
    master_error=None
    if master_db is not None:
        from copy import deepcopy
        from .am_master_adapter import enrich as enrich_master
        try:
            enriched=enrich_master(deepcopy(doc),master_db,synthetic=False)
            doc=enrich(enriched,master_db,synthetic=False)
        except (OSError,ValueError,sqlite3.Error) as error:
            master_error=type(error).__name__
    if 'candidate_filter' not in doc:
        codes={r['stk_cd'] for s in doc['rankings'] for r in s['stocks']}
        doc['candidate_filter']=dict(version='c12-am-candidate-join-v1',at=at.isoformat(),
            decisions={c:dict(state='pending',reasons=['master_evidence_missing']) for c in codes})
    report=briefing.analyze(doc,generated_at=generated.isoformat())
    observations=observe(selection,report['analysis']['rows'],doc['securities'])
    report.update(schema_version='c12-ag-latest-report-v1',as_of=at.isoformat(),generated_at=generated.isoformat(),
        snapshot_selection=selection,telegram_send_enabled=False,
        collection_completed_by={k:(v['snapshot'] or {}).get('db_committed_by') for k,v in selection['selected'].items()},
        status='observation_ready' if observations['watchlist'] else 'data_unavailable',
        candidate_selection_status='review' if report['sectors'] else 'held',observations=observations,
        master_evidence_error=master_error,
        limitations=['market_time_not_provided','candidate_evidence_review_required']+
            ([] if proof.get('unit_runtime_verified') is True else ['runtime_turnover_units_unverified']),
        candidate_confirmed=False)
    report.pop('evaluation_reference',None)
    report['evaluation_reference']=digest(report)
    return report


def briefing_0950(snapshots, *, cycle_completed_at=None, as_of=None, environment, generated_at=None, **kwargs):
    """Offline 09:50 report; pending cycles use earlier completed snapshots.

    as_of is the actual analysis clock, not a fabricated cycle completion.
    There is intentionally no scheduler or send hook.
    """
    reference=instant(as_of or cycle_completed_at)
    completed=instant(cycle_completed_at) if cycle_completed_at else None
    if reference < reference.replace(hour=9,minute=50,second=0,microsecond=0):
        raise ValueError('0950_collection_cycle_not_completed')
    if completed and (completed.date()!=reference.date() or completed>reference
                      or completed<reference.replace(hour=9,minute=50,second=0,microsecond=0)):
        completed=None  # A future completion cannot authorize current data.
    report=analyze(snapshots,as_of=reference.isoformat(),environment=environment,
                   generated_at=generated_at,**kwargs)
    report['briefing_slot']='09:50'
    report['collection_cycle_completed_at']=completed.isoformat() if completed else None
    report['collection_cycle_status']='completed' if completed else 'pending_using_previous_completed_data'
    report.pop('evaluation_reference',None)
    report['evaluation_reference']=digest(report)
    return report


def save_report(report, path):
    """Preserve every intraday result, including held candidates and raw units."""
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='Offline C12-AG report; no send or collect mode')
    p.add_argument('--ranking-db',required=True,type=Path)
    p.add_argument('--completion-log',type=Path)
    p.add_argument('--turnover-db',action='append',default=[],type=Path)
    p.add_argument('--ledger',action='append',default=[],type=Path)
    p.add_argument('--as-of',required=True)
    p.add_argument('--environment',choices=['mock','real'],required=True)
    p.add_argument('--ranking-ttl',type=int,default=360)
    p.add_argument('--turnover-ttl',type=int,default=900)
    p.add_argument('--master-db',type=Path)
    p.add_argument('--unit-evidence',type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    snapshots=load(a.ranking_db,a.turnover_db,day=instant(a.as_of).date().isoformat(),completion_log=a.completion_log,ledgers=a.ledger)
    report=analyze(snapshots,as_of=a.as_of,environment=a.environment,
                   policy=Policy(a.ranking_ttl,a.turnover_ttl),master_db=a.master_db,
                   unit_evidence=json.loads(a.unit_evidence.read_text(encoding='utf-8')) if a.unit_evidence else None)
    save_report(report,a.output)
    print(json.dumps(dict(status=report['status'],candidates=len(report['candidates']),telegram_send_enabled=False)))


if __name__=='__main__': main()
