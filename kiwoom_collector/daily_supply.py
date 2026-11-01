"""Independent daily-history staging, bounded collection and evidence-gated C14 feed."""
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from . import daily_chart as daily
from .daily_diagnostic import inspect_response, Redactor
from .analyst.saved import diagnostic_pages, c12_context
from .analyst.daily_adapter import from_daily_pages
from .analyst.context import analyze_context
from .analyst.chart import known
from .daily_tracking_adapter import ClosingEvidence
from .am_candidate_adapter import normalize_code
from .config import PROJECT_ROOT
from .leader_tracking import timestamp
from .api_resource import api_resource
from .client import KST


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def scope_key(request,origin,basis_id):
    if origin not in ('synthetic_fixture','api_response') or not isinstance(basis_id,str) or not basis_id.strip():
        raise ValueError('explicit_origin_and_basis_id_required')
    return encode([request.environment,origin,request.api_id,request.stock_code,request.adjustment,basis_id])


class SupplyStore:
    """Owned derived store only. Never open an existing arbitrary SQLite database."""
    def __init__(self,root):
        root=Path(root).absolute()
        if root.resolve()!=root:raise ValueError('supply_root_redirected')
        project=PROJECT_ROOT.resolve();allowed=project/'diagnostics'/'daily_supply'
        if any(p.name in ('data','.git','token-cache') for p in (root,*root.parents)):
            raise ValueError('independent_supply_root_required')
        if root==project or project in root.parents:
            if root!=allowed and allowed not in root.parents:raise ValueError('independent_supply_root_required')
        self.root=root;self.path=root/'daily-history.db'
        if root.exists():
            if (root/'.daily-supply-v1').is_symlink() or self.path.is_symlink():raise ValueError('supply_root_redirected')
            if not (root/'.daily-supply-v1').is_file() or not self.path.is_file():raise ValueError('existing_directory_not_owned')
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute('SELECT version FROM supply_meta').fetchone()!=(1,):raise ValueError('supply_schema_mismatch')
        else:
            root.mkdir(parents=True)
            (root/'.daily-supply-v1').write_text('STOCK1 independent daily supply v1\n',encoding='utf-8')
            with closing(sqlite3.connect(self.path)) as db,db:
                db.executescript('''
                CREATE TABLE supply_meta(version INTEGER NOT NULL); INSERT INTO supply_meta VALUES(1);
                CREATE TABLE runs(id TEXT PRIMARY KEY, scope TEXT NOT NULL, summary TEXT NOT NULL);
                CREATE TABLE pages(run_id TEXT NOT NULL, n INTEGER NOT NULL, archive TEXT NOT NULL,
                                   PRIMARY KEY(run_id,n));
                CREATE TABLE bars(scope TEXT NOT NULL, day TEXT NOT NULL, signature TEXT NOT NULL,
                    quality TEXT NOT NULL, run_id TEXT NOT NULL, page_n INTEGER NOT NULL,
                    row_fingerprint TEXT NOT NULL, PRIMARY KEY(scope,day));
                ''')

    def records(self,scope):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            return {r['day']:dict(r) for r in db.execute('SELECT * FROM bars WHERE scope=? ORDER BY day',(scope,))}

    def publish(self,scope,summary,archives,rows,*,replace=False):
        if self.root.resolve()!=self.root or self.path.is_symlink():raise ValueError('supply_root_redirected')
        with closing(sqlite3.connect(self.path,timeout=0)) as db,db:
            db.execute('INSERT INTO runs VALUES(?,?,?)',(summary['run_id'],scope,encode(summary)))
            db.executemany('INSERT INTO pages VALUES(?,?,?)',[(summary['run_id'],i,encode(p)) for i,p in enumerate(archives)])
            if replace:db.execute('DELETE FROM bars WHERE scope=?',(scope,))
            for day,row in rows.items():
                # Conflicting valid historical data was rejected before this transaction.
                db.execute('INSERT INTO bars VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope,day) DO UPDATE SET '
                    'signature=excluded.signature,quality=excluded.quality,run_id=excluded.run_id,'
                    'page_n=excluded.page_n,row_fingerprint=excluded.row_fingerprint WHERE bars.quality!=?',
                    (scope,day,row['signature'],row['quality'],summary['run_id'],row['page_n'],row['row_fingerprint'],'candidate'))

    def archives(self,records):
        result={}
        with closing(sqlite3.connect(self.path)) as db:
            for row in records.values():
                key=(row['run_id'],row['page_n'])
                if key not in result:
                    result[key]=json.loads(db.execute('SELECT archive FROM pages WHERE run_id=? AND n=?',key).fetchone()[0])
        return result


def candidates(document,*,environment,origin,as_of,max_stocks=5):
    if not isinstance(document,dict) or not isinstance(document.get('candidate_filter'),dict):
        raise ValueError('candidate_filter_required')
    block=document['candidate_filter']
    if (block.get('version')!='c12-am-candidate-join-v1' or document.get('environment')!=environment
            or document.get('market')!='KRX' or document.get('source_kind')!=
            ('fixture' if origin=='synthetic_fixture' else 'readonly_adapter')
            or (origin=='api_response' and document.get('data_origin')=='synthetic_fixture')
            or timestamp(block['at'])>timestamp(as_of) or not 1<=max_stocks<=20):
        raise ValueError('candidate_scope_or_budget_invalid')
    normalized={}
    for raw,decision in block['decisions'].items():
        code=normalize_code(raw)
        if code is None or code in normalized or not isinstance(decision,dict):raise ValueError('ambiguous_candidate_code')
        normalized[code]=decision
    selected=sorted(c for c,d in normalized.items() if d.get('state')=='analysis_candidate')
    if not selected or len(selected)>max_stocks:raise ValueError('explicit_candidate_subset_required')
    return selected


def collect(store,request,transport,*,origin,basis_id,as_of,completed_through,
            full_refresh=False,continuity_review=None,max_pages=5,max_calls=20,max_seconds=120):
    """A run is bounded, nonretrying and serialized; only staging storage is writable."""
    key=scope_key(request,origin,basis_id)
    if transport.origin!=origin or not 1<=max_pages<=50 or not 1<=max_calls<=100 or not 0<max_seconds<=600:
        raise ValueError('transport_scope_or_budget_invalid')
    dynamic=as_of is None
    if dynamic and origin!='api_response':raise ValueError('explicit_replay_cutoff_required')
    cutoff=timestamp(as_of) if not dynamic else datetime.now(KST)
    completed=date.fromisoformat(completed_through)
    if completed>cutoff.date() or daily.day(request.base_dt)>cutoff.date():raise ValueError('future_supply_cutoff')
    with api_resource(store.path,request.environment):
        old=store.records(key)
        valid_old=[d for d,r in old.items() if r['quality']=='candidate' and date.fromisoformat(d)<=completed]
        mode='initial' if full_refresh or len(valid_old)<253 else 'incremental'
        overlap=valid_old[-5:] if mode=='incremental' else []
        deadline=time.perf_counter()+max_seconds
        summary=dict(run_id=str(uuid4()),module='daily_supply',mode=mode,environment=request.environment,
            data_origin=origin,stock_code=request.stock_code,basis_id=basis_id,as_of=cutoff.isoformat(),
            completed_through=completed.isoformat(),base_dt=request.base_dt,attempts=0,api_calls=0,
            replay_calls=0,oauth_calls=0,automatic_retries=0,status='failed',reason=None,
            quality_counts={},staged_bars=0,published=False,verified_bars=0)
        summary['started_at']=datetime.now(KST).isoformat()
        archives=[];staged={};seen=set();previous_oldest=None;continuation={};quality=Counter()
        start_calls=transport.calls
        try:
            if mode=='incremental' and request.adjustment=='1':
                proof=continuity_review or {}
                if (not known(proof,cutoff) or proof.get('basis_id')!=basis_id
                        or proof.get('stock_code')!=request.stock_code or proof.get('environment')!=request.environment
                        or proof.get('data_origin')!=origin or proof.get('api_id')!=request.api_id
                        or proof.get('adjustment')!=request.adjustment
                        or proof.get('base_dt')!=request.base_dt or proof.get('previous_latest_day')!=valid_old[-1]
                        or proof.get('corporate_actions_unchanged') is not True):
                    raise ValueError('adjusted_incremental_requires_basis_review')
            for page_n in range(max_pages):
                if summary['attempts']>=max_calls or time.perf_counter()>=deadline:raise ValueError('call_or_time_budget')
                summary['attempts']+=1
                reply=transport.fetch(request,continuation,deadline)
                if time.perf_counter()>deadline:raise ValueError('time_budget')
                if dynamic:
                    received=timestamp(reply['response_received_at'])
                    if received>datetime.now(KST):raise ValueError('receipt_after_analysis_cutoff')
                    cutoff=max(cutoff,received);summary['as_of']=cutoff.isoformat()
                if timestamp(reply['response_received_at'])>cutoff:raise ValueError('receipt_after_analysis_cutoff')
                item=inspect_response(reply['body'],request=request,started_at=reply['request_started_at'],
                    received_at=reply['response_received_at'],http_status=reply['http_status'],
                    headers=reply['headers'],redactor=transport.redactor)
                # Server messages and raw credentials never enter the supply audit log.
                item.pop('return_msg',None)
                if item.get('response_projection'):item['response_projection'].pop('return_msg',None)
                item.update(request=dict(api_id=request.api_id,body=request.body),environment=request.environment,
                    request_started_at=reply['request_started_at'],response_received_at=reply['response_received_at'])
                archive=dict(version='step7-b2-a-v1',environment=request.environment,results=[item],
                    mode='synthetic_fixture' if origin=='synthetic_fixture' else 'live_http_diagnostic',
                    data_origin='synthetic_fixture' if origin=='synthetic_fixture' else
                        {'mock':'mock_server_unverified_source','real':'real_server_unverified_daily_bars'}[request.environment])
                archives.append(archive)
                if not item['b1_parse_success']:raise ValueError('daily_response_rejected')
                page=diagnostic_pages(archive)[0][0]
                raw_headers={k.lower():v for k,v in reply['headers'].items()}
                cursor=raw_headers.get('next-key')
                if page.ordering=='unordered' or 'duplicate_daily_dates' in page.issues:raise ValueError('duplicate_or_unordered_dates')
                if any(i!='daily_order_unverified' for i in page.issues):raise ValueError('page_quality_invalid')
                dated=[r.trade_date for r in page.rows if r.trade_date]
                if dated and previous_oldest and min(dated)>=previous_oldest:raise ValueError('pagination_no_progress')
                if dated:previous_oldest=min(dated)
                for row in page.rows:
                    problems=[i for i in row.issues if 'trde_prica' not in i]
                    if row.volume==0:problems.append('zero_volume_or_halt_review')
                    if row.trade_date and row.trade_date>completed:problems.append('open_or_unreviewed_session')
                    quality.update(problems)
                    if row.trade_date is None:continue
                    day=row.trade_date.isoformat()
                    record=dict(signature=encode([*[str(getattr(row,k)) for k in ('open','high','low','close','volume')],
                                                  row.raw.get('upd_stkpc_tp'),row.raw.get('upd_rt')]),
                        quality='held' if problems else 'candidate',page_n=page_n,row_fingerprint=row.fingerprint)
                    if day in staged and staged[day]['signature']!=record['signature']:raise ValueError('conflicting_duplicate_day')
                    staged.setdefault(day,record)
                usable=[d for d,r in staged.items() if r['quality']=='candidate']
                enough=(len(usable)>=253 if mode=='initial' else all(d in staged for d in overlap))
                if enough or page.cont_yn=='N':
                    if mode=='incremental' and not enough:raise ValueError('incremental_overlap_missing')
                    for d,row in staged.items():
                        if not full_refresh and d in old and old[d]['quality']=='candidate' and old[d]['signature']!=row['signature']:
                            raise ValueError('historical_revision_requires_full_refresh')
                    summary.update(status='success' if enough else 'insufficient_history',published=True)
                    break
                if (page.cont_yn!='Y' or not isinstance(cursor,str) or not 0<len(cursor)<=50
                        or '\r' in cursor or '\n' in cursor):raise ValueError('continuation_unknown_or_missing')
                if cursor in seen:raise ValueError('repeated_cursor')
                seen.add(cursor);continuation={'cont-yn':'Y','next-key':cursor}
            else:raise ValueError('page_budget_exhausted')
        except Exception as exc:
            trusted={'call_or_time_budget','time_budget','receipt_after_analysis_cutoff','daily_response_rejected',
                'duplicate_or_unordered_dates','page_quality_invalid','pagination_no_progress','conflicting_duplicate_day',
                'incremental_overlap_missing','historical_revision_requires_full_refresh','continuation_unknown_or_missing',
                'repeated_cursor','page_budget_exhausted','adjusted_incremental_requires_basis_review'}
            summary.update(status='failed',published=False,reason=str(exc) if type(exc) is ValueError and str(exc) in trusted else 'transport_or_parse_failure')
        summary.update(quality_counts=dict(quality),staged_bars=len(staged),
            finished_at=datetime.now(KST).isoformat(),
            structurally_usable_bars=sum(r['quality']=='candidate' for r in staged.values()),
            oldest_staged_day=min(staged) if staged else None,newest_staged_day=max(staged) if staged else None,
            api_calls=transport.calls-start_calls if origin=='api_response' else 0,
            replay_calls=transport.calls-start_calls if origin=='synthetic_fixture' else 0)
        store.publish(key,summary,archives,staged if summary['published'] else {},replace=full_refresh and summary['published'])
        return summary


def analyze_store(store,request,*,origin,basis_id,as_of,review=None,candidate_document=None):
    """Only bound external closing/calendar/basis reviews can enable C14 values."""
    key=scope_key(request,origin,basis_id);review=review or {}
    expected=dict(environment=request.environment,data_origin=origin,api_id=request.api_id,
        stock_code=request.stock_code,adjustment=request.adjustment,basis_id=basis_id)
    if review and review.get('scope')!=expected:raise ValueError('supply_review_scope_mismatch')
    if review and (review['price_basis'].get('id')!=basis_id or review['price_basis'].get('mode')!=
                   {'0':'original','1':'adjusted'}[request.adjustment]):raise ValueError('supply_review_basis_mismatch')
    with api_resource(store.path,request.environment):
        records=store.records(key);archives=store.archives(records)
    evidence=[ClosingEvidence(**p) for p in review.get('evidence',())]
    candidate=c12_context(candidate_document,request,data_origin=origin,as_of=as_of) if candidate_document else None
    bars=[]
    meta=dict(stock_name=review.get('stock_name',request.stock_code),as_of=as_of,
        price=review.get('price',{}),selection=review.get('selection',{}),
        calendar=review.get('calendar',dict(verified=False,completed_sessions=[])),
        price_basis=review.get('price_basis',dict(verified=False,mode={'0':'original','1':'adjusted'}[request.adjustment])),
        evidence=evidence,context_id=review.get('context_id','unreviewed-daily-supply'),
        expected_session_basis='regular_session' if request.api_id=='ka11081' else 'chart_session',data_origin=origin)
    if candidate and not meta['selection']:
        meta['selection']=dict(stock_code=request.stock_code,source=candidate['source'],
            available_at=candidate['available_at'],candidate_state=candidate['decision']['state'])
    valid_proofs=set()
    for (run,n),archive in archives.items():
        page=diagnostic_pages(archive)[0][0]
        valid_proofs.update((page.fingerprint,r.fingerprint) for r in page.rows)
        part=from_daily_pages([page],**meta)
        for row,converted in zip(page.rows,part['bars']):
            record=records.get(converted['trade_date'])
            if (record and record['quality']=='candidate' and record['run_id']==run and record['page_n']==n
                    and record['row_fingerprint']==row.fingerprint):bars.append(converted)
    if any((e.page_fingerprint,e.row_fingerprint) not in valid_proofs for e in evidence):
        raise ValueError('supply_review_row_binding_mismatch')
    document=dict(schema_version='analyst-input-v1',stock_code=request.stock_code,stock_name=meta['stock_name'],
        as_of=as_of,environment=request.environment,market=request.market,data_origin=origin,
        session_basis=meta['expected_session_basis'],price=meta['price'],selection=meta['selection'],
        calendar=meta['calendar'],price_basis=meta['price_basis'],bars=bars)
    return analyze_context(document,candidate_context=candidate)
