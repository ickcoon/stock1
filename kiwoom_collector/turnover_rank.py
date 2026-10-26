"""Isolated ka10032 test collector. Offline by default; never uses the 0198 Store."""
import argparse
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

import requests

from .client import KST, KiwoomClient, RATE_CODES
from .config import PROJECT_ROOT, load_credentials
from .errors import KiwoomError
from .quotes import CODE

API_ID = 'ka10032'
API_PATH = '/api/dostk/rkinfo'
SPEC_VERSION = '953e5dbff123f437ab4d11a78a95191a685eb51f'
SPEC_URL = 'https://openapi.kiwoom.com/m/guide/apiguide/05/ka10032'
APPLICATION_ID = 0x53544B56
FIELDS = ('stk_cd','now_rank','pred_rank','stk_nm','cur_prc','pred_pre_sig',
          'pred_pre','flu_rt','sel_bid','buy_bid','now_trde_qty','pred_trde_qty','trde_prica')
REQUIRED = {'stk_cd','stk_nm','now_rank','cur_prc','flu_rt','now_trde_qty','trde_prica'}
NUMBER = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?', re.ASCII)
UINT = re.compile(r'\+?[0-9]+', re.ASCII)


def stamp():
    return datetime.now(KST).isoformat(timespec='microseconds')


def dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def instant(value):
    if not isinstance(value,str):return None
    try:
        at=datetime.fromisoformat(value)
        return at.astimezone(KST) if at.tzinfo is not None else None
    except ValueError:return None


@dataclass(frozen=True)
class Profile:
    mrkt_tp: str = '000'
    mang_stk_incls: str = '0'
    stex_tp: str = '1'

    def __post_init__(self):
        if (self.mrkt_tp not in ('000','001','101') or self.mang_stk_incls not in ('0','1')
                or self.stex_tp not in ('1','2','3')):
            raise ValueError('ka10032 시장·관리종목·거래소 조건을 확인하세요.')

    def body(self):
        return dict(mrkt_tp=self.mrkt_tp,mang_stk_incls=self.mang_stk_incls,stex_tp=self.stex_tp)


def normalize(row, profile):
    """Syntax/declared-unit normalization, never empirical or time verification."""
    raw={};issues=[]
    if not isinstance(row,dict):return dict(raw={},normalized={},issues=['invalid_row'],status='invalid')
    for key in FIELDS:
        value=row.get(key)
        if key not in row or value=='':
            if key in REQUIRED:issues.append('missing:'+key)
        elif not isinstance(value,str):issues.append('invalid_type:'+key)
        elif len(value)>(40 if key=='stk_nm' else 20):issues.append('too_long:'+key)
        else:raw[key]=value
    def number(key,integer=False,nonnegative=False):
        value=raw.get(key)
        if value is None:return None
        value=value.strip()
        if not value or not (UINT if integer else NUMBER).fullmatch(value) or (nonnegative and value.startswith('-')):
            issues.append('invalid_number:'+key);return None
        try:
            return Decimal(value)
        except InvalidOperation:
            issues.append('invalid_number:'+key);return None
    def decimal_text(value):
        if value is None:return None
        text=format(value,'f')
        return text.rstrip('0').rstrip('.') if '.' in text else text
    code=raw.get('stk_cd');name=raw.get('stk_nm')
    if code is not None and not CODE.fullmatch(code):issues.append('invalid_code');code=None
    if code and ((code.endswith('_NX') and profile.stex_tp=='1') or (code.endswith('_AL') and profile.stex_tp!='3')):
        issues.append('code_venue_mismatch')
    if name is not None and not name.strip():issues.append('missing:stk_nm')
    rank=number('now_rank',integer=True,nonnegative=True)
    if rank is not None and not 1<=rank<=9223372036854775807:issues.append('invalid_rank');rank=None
    previous=number('pred_rank',integer=True,nonnegative=True)
    amount=number('trde_prica',nonnegative=True)
    rate=number('flu_rt');price=number('cur_prc')
    volume=number('now_trde_qty',integer=True,nonnegative=True)
    prior_volume=number('pred_trde_qty',integer=True,nonnegative=True)
    for key in ('pred_pre','sel_bid','buy_bid'):
        if key in raw:number(key)
    if 'pred_pre_sig' in raw and raw['pred_pre_sig'] not in ('1','2','3','4','5'):
        issues.append('invalid_sign_code')
    with localcontext() as context:
        context.prec=100
        won=decimal_text(amount*Decimal(1000000)) if amount is not None else None
    normalized=dict(version=1,api_id=API_ID,spec_version=SPEC_VERSION,stock_code=code,
        code_status='syntax_only_not_listing_verified',turnover_rank=int(rank) if rank is not None else None,
        previous_day_rank=decimal_text(previous),turnover_source_unit='million_KRW',
        unit_spec_verified=True,unit_evidence=SPEC_URL,unit_runtime_verified=False,
        turnover_won_diagnostic=won,turnover_won_verified=None,
        turnover_basis='unverified_cumulative_scope',server_asof=None,trade_date=None,
        time_status='server_time_missing',current_price_won=None,
        signed_price_diagnostic=decimal_text(price),price_unit='KRW',price_sign_status='unverified',
        change_percent=decimal_text(rate),rate_unit='percent',rate_status='numeric_only_not_time_verified',
        volume_shares=decimal_text(volume),previous_volume_shares=decimal_text(prior_volume),
        volume_basis='unverified_accumulation_scope',requested_market=profile.mrkt_tp,
        requested_venue=profile.stex_tp,managed_stocks='excluded' if profile.mang_stk_incls=='0' else 'included',
        other_security_types='not_individually_filtered_by_this_TR',candidate_confirmed=False)
    return dict(raw=raw,normalized=normalized,issues=list(dict.fromkeys(issues)),
                status='invalid' if code is None or rank is None else 'partial' if issues else 'syntax_valid')


@dataclass
class Capture:
    data: object = field(default=None,repr=False)
    headers: dict = field(default_factory=dict,repr=False)
    http_status: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class FixtureTransport:
    source_kind='fixture'

    def __init__(self,document,profile,environment):
        if (not isinstance(document,dict) or document.get('api_id')!=API_ID
                or document.get('environment')!=environment or document.get('request')!=profile.body()
                or not isinstance(document.get('pages'),list) or not document['pages']):
            raise ValueError('fixture의 API·환경·조회 조건·페이지 목록이 일치하지 않습니다.')
        self.pages=document['pages'];self.index=0

    def fetch(self,profile,continuation):
        if self.index>=len(self.pages):return Capture(error='fixture_exhausted')
        page=self.pages[self.index];self.index+=1
        if not isinstance(page,dict):return Capture(error='invalid_fixture_page')
        return Capture(page.get('body'),page.get('headers',{}),page.get('http_status'),
                       page.get('request_started_at'),page.get('request_finished_at'))


class LiveTransport(KiwoomClient):
    """Explicit future test adapter: one auth attempt, no query retries/re-auth replay."""
    source_kind='live_test'

    def fetch(self,profile,continuation):
        capture=Capture()
        try:
            if self.token is None or self.token.expires_at<=datetime.now(KST)+timedelta(seconds=30):
                if getattr(self,'_ranking_started',False):
                    capture.error='authentication_failed';return capture
                self.authenticate(attempts=1)
        except KiwoomError:
            capture.error='authentication_failed';return capture
        except KeyboardInterrupt:
            capture.error='interrupted';return capture
        def before_send():
            self._ranking_started=True
            capture.started_at=stamp()
        try:
            response=self._request(API_PATH,profile.body(),
                {'api-id':API_ID,'authorization':'Bearer '+self.token.value,**continuation},before_send=before_send)
            capture.http_status=response.status_code
            capture.headers={key:response.headers[key] for key in ('cont-yn','next-key') if key in response.headers}
            try:capture.data=response.json()
            except ValueError:capture.error='invalid_json'
        except requests.RequestException:capture.error='transport_failed'
        except KeyboardInterrupt:capture.error='interrupted'
        finally:capture.finished_at=stamp()
        return capture


SCHEMA='''
CREATE TABLE turnover_runs(
 id TEXT PRIMARY KEY, api_id TEXT NOT NULL CHECK(api_id='ka10032'),
 environment TEXT NOT NULL CHECK(environment IN ('mock','real')),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('fixture','live_test')),
 test_mode INTEGER NOT NULL CHECK(test_mode=1), spec_version TEXT NOT NULL,
 request_json TEXT NOT NULL,profile_hash TEXT NOT NULL,created_at TEXT NOT NULL,finished_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('running','success','empty','partial','error','interrupted')),
 pagination_complete INTEGER NOT NULL DEFAULT 0,issues_json TEXT NOT NULL,
 request_started_at TEXT,request_finished_at TEXT,row_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE turnover_pages(
 run_id TEXT NOT NULL REFERENCES turnover_runs(id),page_no INTEGER NOT NULL,
 started_at TEXT,finished_at TEXT,http_status INTEGER,return_code INTEGER,
 status TEXT NOT NULL,cont_yn TEXT,next_key_hash TEXT,row_count INTEGER NOT NULL,
 issues_json TEXT NOT NULL,response_json TEXT NOT NULL,PRIMARY KEY(run_id,page_no));
CREATE TABLE turnover_rows(
 run_id TEXT NOT NULL,page_no INTEGER NOT NULL,row_no INTEGER NOT NULL,
 stock_code TEXT,turnover_rank INTEGER,validation_status TEXT NOT NULL,
 raw_json TEXT NOT NULL,normalized_json TEXT NOT NULL,issues_json TEXT NOT NULL,
 PRIMARY KEY(run_id,page_no,row_no),
 FOREIGN KEY(run_id,page_no) REFERENCES turnover_pages(run_id,page_no));
'''


def new_path(path):
    path=Path(path).absolute()
    if path.is_symlink() or path.exists():raise ValueError('기존 DB/파일은 열거나 덮어쓰지 않습니다. 새 테스트 DB를 지정하세요.')
    resolved=path.resolve()
    if resolved==(PROJECT_ROOT/'data/stock1.db').resolve() or path.suffix.lower() not in ('.db','.sqlite','.sqlite3'):
        raise ValueError('운영 DB와 다른 새 테스트 SQLite 경로를 지정하세요.')
    return path


def inspect_database(path):
    path=Path(path).resolve()
    if path==(PROJECT_ROOT/'data/stock1.db').resolve():raise ValueError('운영 DB는 이 명령의 대상이 아닙니다.')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID:
            raise ValueError('ka10032 전용 테스트 DB가 아닙니다.')
        return dict(integrity_check=[r[0] for r in db.execute('PRAGMA integrity_check')],
            foreign_key_errors=[list(r) for r in db.execute('PRAGMA foreign_key_check')],
            runs=[dict(zip(('id','status','rows','pagination_complete','source_kind','environment'),r))
                  for r in db.execute('SELECT id,status,row_count,pagination_complete,source_kind,environment FROM turnover_runs')])


def analyze_capture(capture,profile):
    issues=[];rows=[];code=None;cont=None;next_key=None
    start,end=instant(capture.started_at),instant(capture.finished_at)
    if capture.error:issues.append(capture.error if capture.error in ('fixture_exhausted','invalid_fixture_page','authentication_failed','interrupted','invalid_json','transport_failed') else 'transport_failed')
    if start is None or end is None or end<start:issues.append('invalid_request_times')
    if type(capture.http_status) is not int or not 200<=capture.http_status<300:
        issues.append('rate_limited' if capture.http_status==429 else 'authentication_failed' if capture.http_status in (401,403) else 'http_failed')
    data=capture.data
    if not isinstance(data,dict):issues.append('invalid_response')
    else:
        value=data.get('return_code')
        if ((type(value) is int and -2147483648<=value<=2147483647)
                or (isinstance(value,str) and re.fullmatch(r'-?[0-9]+',value) and len(value)<=10)):
            parsed=int(value)
            if -2147483648<=parsed<=2147483647:code=parsed
        if code!=0:issues.append('rate_limited' if code in RATE_CODES else 'mock_unsupported' if code==8104 else 'api_failed' if code is not None else 'invalid_return_code')
    if not issues:
        values=data.get('trde_prica_upper')
        if not isinstance(values,list):issues.append('missing_or_invalid_list')
        else:rows=[normalize(row,profile) for row in values]
    if not isinstance(capture.headers,dict):issues.append('invalid_headers')
    else:
        headers={k.lower():v for k,v in capture.headers.items() if isinstance(k,str)}
        cont=headers.get('cont-yn');next_key=headers.get('next-key')
        if cont not in (None,'','N','Y'):issues.append('invalid_continuation')
        if cont=='Y' and (not isinstance(next_key,str) or not next_key.strip() or len(next_key)>50):
            issues.append('invalid_continuation_key');next_key=None
    safe_data=dict(return_code=code,trde_prica_upper=[r['raw'] for r in rows])
    return dict(rows=rows,issues=list(dict.fromkeys(issues)),return_code=code,
        cont_yn=cont if cont in ('','N','Y') else None,next_key=next_key if cont=='Y' else None,
        response=safe_data,started_at=start.isoformat() if start else None,finished_at=end.isoformat() if end else None)


def collect(path,transport,profile=None,environment='mock',max_pages=1):
    profile=profile or Profile()
    if environment not in ('mock','real') or type(max_pages) is not int or not 1<=max_pages<=10:
        raise ValueError('환경 또는 로컬 페이지 상한(1~10)을 확인하세요.')
    if transport.source_kind not in ('fixture','live_test'):raise ValueError('독립 테스트 전송원만 지원합니다.')
    if transport.source_kind=='live_test' and transport.credentials.environment!=environment:
        raise ValueError('요청 환경과 인증정보 환경이 다릅니다.')
    target=new_path(path);target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb'):pass
    run_id=str(uuid4());pages=[];entries=[];issues=[];continuation={};seen_keys=set();complete_chain=False
    with closing(sqlite3.connect(target)) as db:
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA application_id='+str(APPLICATION_ID));db.executescript(SCHEMA)
        db.execute('INSERT INTO turnover_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (run_id,API_ID,environment,transport.source_kind,1,SPEC_VERSION,dump(profile.body()),digest(dump(profile.body())),stamp(),None,'running',0,'[]',None,None,0))
        db.commit()
        for index in range(1,max_pages+1):
            try:capture=transport.fetch(profile,continuation)
            except KeyboardInterrupt:capture=Capture(error='interrupted')
            except Exception:capture=Capture(error='transport_failed')
            if not isinstance(capture,Capture):capture=Capture(error='transport_failed')
            page=analyze_capture(capture,profile)
            previous_end=instant(pages[-1]['finished_at']) if pages else None
            current_start,current_end=instant(page['started_at']),instant(page['finished_at'])
            if previous_end and current_start and current_start<previous_end:
                page['issues'].append('page_time_overlap_or_reverse')
            if current_start and current_end and current_start.date()!=current_end.date():
                page['issues'].append('request_crosses_day_boundary')
            if pages and current_start and instant(pages[0]['started_at']) and current_start.date()!=instant(pages[0]['started_at']).date():
                page['issues'].append('pages_cross_day_boundary')
            pages.append(page)
            page_issues=list(page['issues'])
            if any(r['issues'] for r in page['rows']):page_issues.append('row_validation_failed')
            state='error' if page['issues'] else 'partial' if page_issues else 'success' if page['rows'] else 'empty'
            db.execute('INSERT INTO turnover_pages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (run_id,index,page['started_at'],page['finished_at'],capture.http_status if type(capture.http_status) is int and 100<=capture.http_status<=599 else None,
                 page['return_code'],state,page['cont_yn'],digest(page['next_key']) if isinstance(page['next_key'],str) else None,
                 len(page['rows']),dump(page_issues),dump(page['response'])))
            for ordinal,row in enumerate(page['rows'],1):
                n=row['normalized'];entries.append((index,ordinal,row))
                db.execute('INSERT INTO turnover_rows VALUES(?,?,?,?,?,?,?,?,?)',
                    (run_id,index,ordinal,n.get('stock_code'),n.get('turnover_rank'),row['status'],dump(row['raw']),dump(n),dump(row['issues'])))
            db.commit();issues.extend(page_issues)
            if page['issues']:break
            if page['cont_yn']!='Y':complete_chain=True;break
            key=page['next_key']
            if key in seen_keys:issues.append('repeated_continuation_key');break
            if not page['rows']:issues.append('empty_continuation_page');break
            if index==max_pages:issues.append('page_limit_reached');break
            seen_keys.add(key);continuation={'cont-yn':'Y','next-key':key}
        for field_name in ('stock_code','turnover_rank'):
            groups={}
            for page_no,row_no,row in entries:
                value=row['normalized'].get(field_name)
                if value is not None:groups.setdefault(value,[]).append((page_no,row_no,row))
            for duplicates in groups.values():
                if len(duplicates)<2:continue
                issue='duplicate_'+field_name;issues.append(issue)
                for page_no,row_no,row in duplicates:
                    row['issues']=list(dict.fromkeys(row['issues']+[issue]));row['status']='invalid'
                    db.execute('UPDATE turnover_rows SET validation_status=?,issues_json=? WHERE run_id=? AND page_no=? AND row_no=?',
                        ('invalid',dump(row['issues']),run_id,page_no,row_no))
                    old=json.loads(db.execute('SELECT issues_json FROM turnover_pages WHERE run_id=? AND page_no=?',(run_id,page_no)).fetchone()[0])
                    db.execute("UPDATE turnover_pages SET status='partial',issues_json=? WHERE run_id=? AND page_no=?",(dump(list(dict.fromkeys(old+[issue]))),run_id,page_no))
        issues=list(dict.fromkeys(issues))
        status=('interrupted' if 'interrupted' in issues else 'partial' if issues and entries else 'error' if issues
                else 'success' if entries else 'empty')
        started=next((p['started_at'] for p in pages if p['started_at']),None)
        finished=next((p['finished_at'] for p in reversed(pages) if p['finished_at']),None)
        db.execute('UPDATE turnover_runs SET status=?,finished_at=?,pagination_complete=?,issues_json=?,request_started_at=?,request_finished_at=?,row_count=? WHERE id=?',
            (status,stamp(),int(complete_chain),dump(issues),started,finished,len(entries),run_id));db.commit()
    return dict(database=str(target),run_id=run_id,api_id=API_ID,status=status,pages=len(pages),rows=len(entries),
                pagination_complete=complete_chain,issues=issues,source_kind=transport.source_kind,environment=environment)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for command in ('plan','offline','live-test'):
        p=commands.add_parser(command)
        p.add_argument('--environment',choices=('mock','real'),default='mock')
        p.add_argument('--market',choices=('000','001','101'),default='000')
        p.add_argument('--managed',choices=('0','1'),default='0')
        p.add_argument('--venue',choices=('1','2','3'),default='1')
        p.add_argument('--max-pages',type=int,default=1)
        if command!='plan':p.add_argument('--db',required=True)
        if command=='offline':p.add_argument('--fixture',required=True)
        if command=='live-test':p.add_argument('--network-approved',action='store_true')
    p=commands.add_parser('inspect');p.add_argument('--db',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='inspect':result=inspect_database(args.db)
        else:
            profile=Profile(args.market,args.managed,args.venue)
            if not 1<=args.max_pages<=10:raise ValueError('로컬 페이지 상한은 1~10입니다.')
            if args.command=='plan':
                result=dict(api_id=API_ID,method='POST',path=API_PATH,environment=args.environment,
                    request=profile.body(),max_pages=args.max_pages,network_calls=0,oauth_calls=0,
                    unit_spec='million_KRW',runtime_unit_verified=False,server_asof=None,
                    exclusions='관리종목만 요청 필터로 제어; ETF/ETN/우선주/SPAC 개별 제외 없음')
            else:
                new_path(args.db) # Reject every existing DB before credentials or network.
                if args.command=='offline':
                    fixture=json.loads(Path(args.fixture).read_text(encoding='utf-8-sig'))
                    transport=FixtureTransport(fixture,profile,args.environment)
                    result=collect(args.db,transport,profile,args.environment,args.max_pages)
                else:
                    if not args.network_approved:raise ValueError('실제 API/OAuth는 별도 승인 후 --network-approved로만 실행합니다.')
                    credentials=load_credentials()
                    if credentials.environment!=args.environment:raise ValueError('인증정보 환경과 --environment가 일치해야 합니다.')
                    transport=LiveTransport(credentials)
                    try:result=collect(args.db,transport,profile,args.environment,args.max_pages)
                    finally:transport.close()
    except (ValueError,OSError,sqlite3.Error):
        parser.exit(1,'ka10032 실행 조건·fixture·새 DB 경로 또는 저장 상태를 확인하세요. 원문/인증정보는 출력하지 않습니다.\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('status') in (None,'success') else 3 if result['status']=='empty' else 130 if result['status']=='interrupted' else 2


if __name__=='__main__':raise SystemExit(main())
