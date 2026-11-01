"""Independent, default-plan daily supply CLI; replay or separately authorized HTTP."""
import argparse
from copy import deepcopy
from datetime import datetime, date
import json
import os
import sqlite3
from pathlib import Path
import time

from . import daily_chart as daily
from .daily_supply import SupplyStore, collect, candidates, analyze_store
from .analyst.saved import read_json
from .daily_diagnostic import Redactor
from .client import KiwoomClient, KST
from .catalog_auth import credentials


def identity(request):
    return dict(api_id=request.api_id,stock_code=request.stock_code,environment=request.environment,
                base_dt=request.base_dt,adjustment=request.adjustment)


class ReplayTransport:
    origin='synthetic_fixture'
    def __init__(self,document):
        if document.get('schema_version')!='daily-supply-replay-v1':raise ValueError('explicit_replay_fixture_required')
        self.items=iter(document['responses']);self.calls=0;self.redactor=Redactor()
    def fetch(self,request,continuation,deadline):
        if time.perf_counter()>=deadline:raise ValueError('time_budget')
        self.calls+=1;item=next(self.items)
        if item['request']!=identity(request) or item['continuation']!=continuation:raise ValueError('fixture_request_mismatch')
        return deepcopy(item['reply'])
    def close(self):pass


class HTTPTransport:
    origin='api_response'
    api_ids=('ka10081',)
    def __init__(self,creds,plans,access_token,*,approved=False):
        if approved is not True or not isinstance(access_token,str) or not access_token or len(access_token)>4096 or any(c.isspace() for c in access_token):
            raise ValueError('explicit_approval_and_existing_token_required')
        if any(p.environment!=creds.environment or p.api_id not in self.api_ids for p in plans):raise ValueError('http_plan_scope_invalid')
        if creds.base_url!={'mock':'https://mockapi.kiwoom.com','real':'https://api.kiwoom.com'}[creds.environment]:
            raise ValueError('http_host_not_allowed')
        self.allowed={json.dumps(identity(p),sort_keys=True) for p in plans}
        self.client=KiwoomClient(creds);self.calls=0;self._access_token=access_token
        self.client.session.trust_env=False;self.client.session.verify=True
        from requests.adapters import HTTPAdapter
        self.client.session.mount('https://',HTTPAdapter(max_retries=0))
        self.redactor=Redactor(creds.app_key,creds.secret_key,access_token)
    def fetch(self,request,continuation,deadline):
        if json.dumps(identity(request),sort_keys=True) not in self.allowed:raise ValueError('http_plan_scope_invalid')
        if set(continuation) not in (set(),{'cont-yn','next-key'}):raise ValueError('continuation_invalid')
        stamps={}
        def before_send():
            if time.perf_counter()>=deadline:raise ValueError('time_budget')
            self.calls+=1;stamps['request_started_at']=datetime.now(KST).isoformat()
        response=self.client._request(daily.API_PATH,request.body,
            {'api-id':request.api_id,'authorization':'Bearer '+self._access_token,**continuation},before_send=before_send)
        return dict(body=response.json(),headers=response.headers,http_status=response.status_code,
            request_started_at=stamps['request_started_at'],response_received_at=datetime.now(KST).isoformat())
    def close(self):
        self._access_token=None;self.redactor.secrets.clear();self.client.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--c12-json',type=Path,required=True)
    p.add_argument('--base-date',required=True)
    p.add_argument('--completed-through',required=True,help='Explicit last completed session, YYYY-MM-DD; never inferred')
    p.add_argument('--environment',choices=('mock','real'),required=True)
    p.add_argument('--adjustment',choices=('0','1'),default='1')
    p.add_argument('--basis-id',required=True,help='Stable reviewed price-basis version, never a verification flag')
    p.add_argument('--store-root',type=Path)
    p.add_argument('--as-of',help='Required for replay; live records actual receipt times')
    p.add_argument('--replay-json',type=Path)
    p.add_argument('--live',action='store_true')
    p.add_argument('--execute-approved',action='store_true')
    p.add_argument('--quota-reviewed',action='store_true')
    p.add_argument('--credentials-env-file',type=Path)
    p.add_argument('--reviews-json',type=Path,help='Optional code->bound supply review mapping')
    p.add_argument('--full-refresh',action='store_true')
    p.add_argument('--max-pages',type=int,default=5)
    p.add_argument('--max-calls',type=int,default=20)
    p.add_argument('--max-stocks',type=int,default=5)
    p.add_argument('--max-seconds',type=float,default=120)
    args=p.parse_args(argv);transport=None
    try:
        if args.live and args.replay_json:raise ValueError('exclusive_transport_required')
        if args.live and (not args.execute_approved or not args.quota_reviewed or not args.credentials_env_file):
            raise ValueError('live_approval_credentials_required')
        if args.live and args.as_of:raise ValueError('live_uses_actual_receipt_cutoff')
        if args.replay_json and not args.as_of:raise ValueError('replay_asof_required')
        if not (1<=args.max_pages<=50 and 1<=args.max_calls<=100 and 1<=args.max_stocks<=20 and 0<args.max_seconds<=600):
            raise ValueError('budgets_invalid')
        daily.day(args.base_date)
        cutoff=args.as_of or datetime.now(KST).isoformat()
        if date.fromisoformat(args.completed_through)>datetime.fromisoformat(cutoff).date():raise ValueError('future_completion_date')
        if daily.day(args.base_date)>datetime.fromisoformat(cutoff).date():raise ValueError('future_base_date')
        document=read_json(args.c12_json)[0]
        origin='synthetic_fixture' if args.replay_json or document.get('source_kind')=='fixture' else 'api_response'
        codes=candidates(document,environment=args.environment,origin=origin,as_of=cutoff,max_stocks=args.max_stocks)
        plans=[daily.DailyRequest('ka10081',c,args.environment,args.base_date,args.adjustment) for c in codes]
        if not args.live and not args.replay_json:
            print(json.dumps(dict(mode='plan_only',requests=[identity(r) for r in plans],oauth_calls=0,api_calls=0,
                                  minimum_completed_bars=253,store_opened=False)));return 0
        if not args.store_root:raise ValueError('independent_store_required')
        if args.live:
            if origin!='api_response':raise ValueError('fixture_cannot_enable_live')
            token=os.environ.get('KIWOOM_ACCESS_TOKEN')
            if not token:raise ValueError('existing_token_required_no_oauth')
            transport=HTTPTransport(credentials(args.credentials_env_file,environment=args.environment),plans,token,approved=True)
        else:transport=ReplayTransport(read_json(args.replay_json)[0])
        reviews=read_json(args.reviews_json)[0] if args.reviews_json else {}
        if not isinstance(reviews,dict):raise ValueError('review_mapping_required')
        store=SupplyStore(args.store_root);runs=[];attempts=0;deadline=time.perf_counter()+args.max_seconds
        for request in plans:
            remaining=args.max_calls-attempts;seconds=deadline-time.perf_counter()
            if remaining<=0 or seconds<=0:break
            run=collect(store,request,transport,origin=origin,basis_id=args.basis_id,as_of=args.as_of,
                completed_through=args.completed_through,full_refresh=args.full_refresh,
                continuity_review=reviews.get(request.stock_code,{}).get('continuity'),
                max_pages=args.max_pages,max_calls=remaining,max_seconds=seconds)
            runs.append(run);attempts+=run['attempts']
            if run['status']=='failed':break
            report=analyze_store(store,request,origin=origin,basis_id=args.basis_id,as_of=run['as_of'],
                review=reviews.get(request.stock_code),candidate_document=document)
            run['analysis_bars']=len(report['analysis']['evidence']['bars'])
            run['high52_status']=report['high52']['status']
            with (store.root/(run['run_id']+'.chart.json')).open('x',encoding='utf-8') as target:
                json.dump(report,target,ensure_ascii=False,indent=2,allow_nan=False)
        summary=dict(module='daily_supply',runs=runs,oauth_calls=0,api_calls=sum(r['api_calls'] for r in runs),
                     not_attempted_codes=codes[len(runs):],failed_count=sum(r['status']=='failed' for r in runs),
                     all_candidates_attempted=len(runs)==len(plans))
        with (store.root/('batch-'+str(time.time_ns())+'.json')).open('x',encoding='utf-8') as target:
            json.dump(summary,target,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps(summary,ensure_ascii=False))
        return 0 if summary['all_candidates_attempted'] and all(r['status']=='success' for r in runs) else 2
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
        p.error('Invalid daily supply input, approval, independent store or review; no automatic retry/OAuth')
    finally:
        if transport:transport.close()


if __name__=='__main__':raise SystemExit(main())
