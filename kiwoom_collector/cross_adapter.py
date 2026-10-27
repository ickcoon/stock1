"""Independent, read-only Step 5-E diagnostic adapter; never confirms candidates."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .cross_analysis import number, rank, timestamp


@contextmanager
def readonly_database(path):
    """No copying/checkpointing. Refuse WAL/journal; on Windows deny writers.

    immutable is safe only while the source is stable. Keep a Windows read-only
    handle with FILE_SHARE_READ (no write/delete sharing) until SQLite closes.
    """
    path = Path(path).resolve(strict=True)
    handle = None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        handle = create(str(path),0x80000000,1,None,3,0x80,None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError('Source busy; cannot guarantee read-only stable access')
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
    db = None
    try:
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
            raise ValueError('Active sidecars: stop; never checkpoint or copy the source')
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        db = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        def authorize(action, _a, _b, _c, _d):
            return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION) else sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        provenance = dict(path=str(path),sha256=before,sqlite_mode='ro',immutable=True,
                          writer_exclusion=os.name=='nt',sidecars_absent=True)
        yield db, provenance
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise RuntimeError('Source changed during analysis')
        provenance['hash_unchanged'] = True
    finally:
        if db is not None: db.close()
        if handle is not None: close(handle)


def adapt(ranking_db, turnover_db, *, ranking_at, environment='mock', registry=None, http_evidence=None):
    if environment not in ('mock','real') or timestamp(ranking_at) is None:
        raise ValueError('Explicit environment and timezone-aware ranking_at required')
    registry = registry or {}
    with readonly_database(ranking_db) as (db, provenance_a):
        runs = [dict(r) for r in db.execute("SELECT id,collected_at,scheduled_at,environment,api_id,qry_tp,test_mode,status,record_count FROM collections WHERE environment=? AND api_id='ka00198' AND status='success'",(environment,))]
        current = [r for r in runs if timestamp(r['collected_at']) == timestamp(ranking_at)]
        if len(current) != 1: raise ValueError('Select exactly one successful ranking observation')
        a = current[0]
        def read_rows(identifier):
            return [dict(r) for r in db.execute('SELECT rank,stk_cd,stk_nm,raw_json FROM ranking_records WHERE collection_id=? ORDER BY rank',(identifier,))]
        a['rows'] = read_rows(a['id'])
        a['test_mode'] = bool(a['test_mode'])
        a['source_kind'] = 'scheduled_collection' if a['scheduled_at'] else 'collection'
        a['market'] = None  # ka00198 does not store equivalent ka10032 filters.
        a['request_started_at'] = None
        a['response_received_at'] = None
        a['market_asof'] = None
        history = []
        at = timestamp(a['collected_at'])
        for r in runs:
            rt = timestamp(r['collected_at'])
            if rt and rt.date()==at.date() and rt<at and r['qry_tp']==a['qry_tp'] and bool(r['test_mode'])==a['test_mode']:
                history.append(dict(r,rows=read_rows(r['id'])))
        sectors = {}
        for r in db.execute('SELECT t.stock_code,s.sector_name,t.source,t.updated_at FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id ORDER BY s.sector_name'):
            sectors.setdefault(r['stock_code'],[]).append(dict(r))
    with readonly_database(turnover_db) as (db, provenance_b):
        runs = [dict(r) for r in db.execute('SELECT * FROM turnover_runs')]
        if len(runs)!=1: raise ValueError('Independent DB must contain exactly one selected run')
        b = runs[0]
        if b['environment'] != environment: raise ValueError('Mixed mock/real comparison forbidden')
        b['test_mode'] = bool(b['test_mode'])
        b['market'] = json.loads(b.pop('request_json'))
        b['collected_at'] = b['request_started_at']
        b['response_received_at'] = None
        b['market_asof'] = None
        b['pages'] = [dict(r) for r in db.execute('SELECT page_no,http_status,return_code,cont_yn,row_count,started_at,finished_at FROM turnover_pages WHERE run_id=? ORDER BY page_no',(b['id'],))]
        b['rows'] = [json.loads(r[0]) for r in db.execute('SELECT raw_json FROM turnover_rows WHERE run_id=? ORDER BY page_no,row_no',(b['id'],))]
    if http_evidence is not None:
        expected = 'https://mockapi.kiwoom.com' if environment=='mock' else 'https://api.kiwoom.com'
        if http_evidence.get('environment')!=environment or http_evidence.get('host')!=expected:
            raise ValueError('HTTP evidence source mismatch')
        events=[r for r in http_evidence.get('requests',[]) if r.get('api')=='ka10032' and r.get('request_started_at')==b['request_started_at']]
        if len(events)!=1: raise ValueError('HTTP evidence must bind to selected request')
        b['response_received_at']=events[0].get('response_received_at')
        b['http_date']=events[0].get('http_date')
    return dict(schema_version=2,mode='diagnostic_only',ranking=a,turnover=b,history=history,
                sectors=sectors,code_registry=registry,provenance={'ranking':provenance_a,'turnover':provenance_b})


def code_status(code, registry):
    if not isinstance(code,str): return 'invalid_type'
    if re.fullmatch(r'[0-9]{6}',code): return 'legacy_numeric_format'
    if re.fullmatch(r'[0-9A-Z]{6}',code) is None: return 'invalid_format'
    item=registry.get(code,{})
    if item.get('verified') is True and item.get('source') and item.get('security_type') in ('ETF','ETN','common_stock','preferred_stock','SPAC','REIT'):
        return 'official_evidence'
    return 'official_validity_unverified'


def index_rows(rows, field, registry, maximum=None):
    codes=Counter(r.get('stk_cd') for r in rows if isinstance(r,dict) and isinstance(r.get('stk_cd'),str))
    ranks=Counter(rank(r.get(field),maximum) for r in rows if isinstance(r,dict))
    accepted,review={},[]
    for i,r in enumerate(rows):
        code=r.get('stk_cd') if isinstance(r,dict) else None
        position=rank(r.get(field),maximum) if isinstance(r,dict) else None
        state=code_status(code,registry)
        reasons=[]
        if state not in ('legacy_numeric_format','official_evidence'): reasons.append(state)
        if isinstance(code,str) and codes[code]>1: reasons.append('duplicate_code')
        if position is None: reasons.append('invalid_rank')
        elif ranks[position]>1: reasons.append('duplicate_rank')
        if reasons: review.append(dict(row_index=i,stock_code=code,row=r,reasons=reasons))
        else: accepted[code]=r
    return accepted,review


def analyze_pair(document, *, min_turnover_eok=500):
    if type(min_turnover_eok) is not int or min_turnover_eok not in (500,1000):
        raise ValueError('Choose 500 or 1000 eok KRW')
    a,b=document['ranking'],document['turnover']
    if a['environment'] not in ('mock','real') or a['environment']!=b['environment']:
        raise ValueError('Mixed mock/real comparison forbidden')
    registry=document.get('code_registry',{})
    aa,ar=index_rows(a['rows'],'rank',registry,20)
    bb,br=index_rows(b['rows'],'now_rank',registry)
    at,bt=timestamp(a.get('collected_at')),timestamp(b.get('request_started_at'))
    delta=(bt-at).total_seconds() if at and bt else None
    window=bool(delta is not None and abs(delta)<=300 and at.date()==bt.date())
    repetition=Counter(c for c,r in aa.items() if a.get('status')=='success' and rank(r['rank'],20)<=5)
    seen={a['id']};accepted_history=[];rejected_history=[]
    for h in document.get('history',[]):
        ht=timestamp(h.get('collected_at'))
        if (not ht or not at or ht>=at or ht.date()!=at.date() or h.get('id') in seen or not h.get('id')
            or h.get('environment')!=a['environment'] or h.get('qry_tp')!=a.get('qry_tp') or bool(h.get('test_mode'))!=bool(a.get('test_mode')) or h.get('status')!='success'):
            rejected_history.append(h.get('id'));continue
        hi,hr=index_rows(h['rows'],'rank',registry,20)
        seen.add(h['id'])
        if any(reason!='official_validity_unverified' for row in hr for reason in row['reasons']):
            rejected_history.append(h['id']);continue
        accepted_history.append(h['id'])
        repetition.update(c for c,r in hi.items() if rank(r['rank'],20)<=5)
    threshold=Decimal(min_turnover_eok)*Decimal(100000000)
    excluded=[];selected={};filter_reasons={};rank_count=0
    for r in b['rows']:
        code=r.get('stk_cd');position=rank(r.get('now_rank'));amount=number(r.get('trde_prica'))
        reasons=[]
        if position is None: reasons.append('invalid_rank')
        elif position>40: reasons.append('rank_over_40')
        else: rank_count+=1
        if amount is None or amount<0: reasons.append('invalid_turnover')
        else:
            with localcontext() as ctx:
                ctx.prec=100
                if amount*Decimal(1000000)<threshold: reasons.append('below_min_turnover')
        filter_reasons[code]=reasons
        if reasons:excluded.append(dict(stock_code=code,rank=position,turnover_raw=r.get('trde_prica'),reasons=reasons))
        elif code in bb:selected[code]=r
    rows=[]
    for code,r in sorted(aa.items(),key=lambda x:rank(x[1]['rank'],20)):
        t=bb.get(code);entry=registry.get(code,{})
        sectors=sorted({s['sector_name'] for s in document.get('sectors',{}).get(code,[])})
        amount=number(t.get('trde_prica')) if t else None
        with localcontext() as ctx:
            ctx.prec=100
            won=format(amount*Decimal(1000000),'f') if amount is not None and amount>=0 else None
        reasons=['market_asof_unverified','market_conditions_unverified','unit_runtime_unverified','coverage_partial' if not b.get('pagination_complete') else 'coverage_unverified']
        if not window:reasons.append('outside_300_seconds_or_unknown')
        if bool(a.get('test_mode'))!=bool(b.get('test_mode')):reasons.append('operational_test_diagnostic_only')
        if t is None:reasons.append('missing_turnover_match')
        if not sectors:reasons.append('unclassified_sector')
        kind=entry.get('security_type','unknown')
        if kind=='unknown':reasons.append('security_type_unverified')
        if t and any(number(t.get(k)) is None for k in ('cur_prc','flu_rt','now_trde_qty')):reasons.append('invalid_market_values')
        excluded_for=filter_reasons.get(code,[]) if t else ['missing_turnover_match']
        rows.append(dict(stock_code=code,stock_name=r.get('stk_nm'),turnover_stock_name=t.get('stk_nm') if t else None,
            code_status=code_status(code,registry),security_type=kind,sectors=sectors,
            ranking_0198=rank(r['rank'],20),top5_appearances=repetition[code],
            turnover_rank=rank(t['now_rank']) if t else None,turnover_raw=t.get('trde_prica') if t else None,
            turnover_declared_unit='million_KRW',turnover_won_diagnostic=won,unit_runtime_verified=False,
            price_raw=t.get('cur_prc') if t else None,change_percent_raw=t.get('flu_rt') if t else None,volume_raw=t.get('now_trde_qty') if t else None,
            delta_seconds=delta,collection_window_provisional=window,market_time_verified=False,
            filter_selected=code in selected,filter_exclusion_reasons=excluded_for,
            review_priority='TOP5 우선 검토' if rank(r['rank'],20)<=5 else '보조 관찰',
            cross_status='검증 보류',reasons=reasons,candidate_confirmed=False))
    common=[r for r in rows if r['turnover_rank'] is not None]
    markets=[s.get('market') for s in (a,b)]
    market_comparison=('unverified' if any(not isinstance(m,dict) or any(k not in m for k in ('mrkt_tp','mang_stk_incls','stex_tp')) for m in markets)
                       else 'declared_match_unverified' if markets[0]==markets[1] else 'mismatch')
    counts=dict(ranking_input=len(a['rows']),turnover_input=len(b['rows']),turnover_rank_40_or_better=rank_count,
        turnover_meeting_numeric_filters=len(b['rows'])-len(excluded),turnover_selected_valid_codes=len(selected),
        ranking_code_review=len(ar),turnover_code_review=len(br),common_before_filter=len(common),
        common_after_filter=sum(r['filter_selected'] for r in common),
        raw_exact_code_overlap=len({r.get('stk_cd') for r in a['rows'] if isinstance(r,dict) and isinstance(r.get('stk_cd'),str)} & {r.get('stk_cd') for r in b['rows'] if isinstance(r,dict) and isinstance(r.get('stk_cd'),str)}))
    return dict(schema_version=2,mode='diagnostic_only',sources={'ranking':a,'turnover':b},provenance=document.get('provenance',{}),
        policy=dict(max_rank=40,min_turnover_eok=min_turnover_eok,min_turnover_won=str(threshold),max_collection_delta_seconds=300,filter_unit_basis='specification_only',candidate_confirmed=False),
        counts=counts,rows=rows,common_rows=common,excluded_turnover=excluded,
        review=dict(ranking=ar,turnover=br),history=dict(accepted=accepted_history,rejected=rejected_history,success_observations=1+len(accepted_history)),
        missing_turnover_codes=sorted(set(aa)-set(bb)),turnover_only_codes=sorted(set(bb)-set(aa)),
        timing=dict(delta_seconds=delta,collection_window_provisional=window,market_time_verified=False),
        market_match=market_comparison,unit_runtime_verified=False,candidate_confirmed=False)


def render(report):
    def cell(v):return str(v if v is not None else '미확인').replace('|','\\|').replace('\n',' ').replace('\r',' ')
    headers=['코드','종목명','섹터','유형','0198','TOP5 횟수','0186','거래대금 원문(백만원)','등락률(%)','시차(초)','필터','검증 상태']
    lines=['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']
    for r in report['common_rows']:
        values=[r['stock_code'],r['stock_name'],', '.join(r['sectors']) or '미분류',r['security_type'],r['ranking_0198'],r['top5_appearances'],r['turnover_rank'],r['turnover_raw'],r['change_percent_raw'],r['delta_seconds'],'통과(명세 기준)' if r['filter_selected'] else ', '.join(r['filter_exclusion_reasons']),r['cross_status']]
        lines.append('| '+' | '.join(cell(v) for v in values)+' |')
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ranking-db',required=True,type=Path)
    p.add_argument('--turnover-db',required=True,type=Path)
    p.add_argument('--ranking-at',required=True)
    p.add_argument('--environment',choices=('mock','real'),default='mock')
    p.add_argument('--registry',type=Path)
    p.add_argument('--http-evidence',type=Path)
    p.add_argument('--min-turnover-eok',type=int,choices=(500,1000),default=500)
    p.add_argument('--format',choices=('json','markdown'),default='json')
    args=p.parse_args(argv)
    registry=json.loads(args.registry.read_text(encoding='utf-8-sig')) if args.registry else {}
    evidence=json.loads(args.http_evidence.read_text(encoding='utf-8-sig')) if args.http_evidence else None
    document=adapt(args.ranking_db,args.turnover_db,ranking_at=args.ranking_at,environment=args.environment,registry=registry,http_evidence=evidence)
    result=analyze_pair(document,min_turnover_eok=args.min_turnover_eok)
    print(render(result) if args.format=='markdown' else json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
