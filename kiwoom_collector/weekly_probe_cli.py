"""Weekly capture/replay -> existing hybrid JSON. Default is a zero-call plan.

python -m kiwoom_collector.weekly_probe_cli --stock-code 005930 --environment real
    --base-date YYYYMMDD --adjustment 1
Execution also requires --daily-input analyst-input-v1.json --output-dir NEW_PATH
and one of --replay-json FIXTURE, --saved-weekly-json CAPTURE, or explicitly
approved --live. Live requires KIWOOM_ACCESS_TOKEN and the timezone-aware
KIWOOM_ACCESS_TOKEN_EXPIRES_AT (more than 10 minutes remaining); OAuth
issuance is disabled. Review captured fingerprints offline via --weekly-review-json.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path

from . import weekly_chart as weekly
from .daily_supply_cli import HTTPTransport,ReplayTransport,identity
from .daily_diagnostic import output_root,Redactor
from .catalog_auth import credentials
from .client import Token,KST
from .token_manager import TokenManager
from .errors import TokenManagementError
from .analyst.saved import read_json
from .analyst.context import analyze_context
from .analyst.hybrid import analyze_hybrid,WeeklyEvidence
from .leader_tracking import timestamp


def _no_oauth():raise TokenManagementError('weekly_oauth_disabled')


class WeeklyHTTPTransport(HTTPTransport):
    """Same scoped/paced/TLS transport, separate weekly-only allowlist."""
    api_ids=('ka10082',)
    def __init__(self,creds,request,access_token,expires_at,*,approved=False):
        if approved is not True:raise ValueError('weekly_approval_required')
        self.manager=TokenManager(_no_oauth,credentials=creds)
        try:
            token=self.manager.get_token(existing=Token(access_token,timestamp(expires_at)),attempts=1)
            super().__init__(creds,[request],token.value,approved=True)
        except Exception:
            self.manager.close()
            raise ValueError('valid_existing_token_and_reviewed_expiry_required_no_oauth') from None
    def fetch(self,request,continuation,deadline):
        # The issuer cannot make network calls; the manager has no shared cache.
        self._access_token=self.manager.get_token(attempts=1).value
        return super().fetch(request,continuation,deadline)
    def close(self):
        self.manager.close();super().close()


def page_record(page,redactor):
    body=page.raw
    safe=redactor.body({**body,'stk_dt_pole_chart_qry':body[weekly.LIST_KEY]})
    safe[weekly.LIST_KEY]=safe.pop('stk_dt_pole_chart_qry');safe.pop('return_msg',None)
    return dict(request=identity(page.request),body=safe,
        request_started_at=page.request_started_at.isoformat(),response_received_at=page.response_received_at.isoformat(),
        source=page.source,cont_yn=page.cont_yn,next_key=redactor.scalar(page.next_key),response_api_id=page.response_api_id)


def restore_page(record,request):
    if record['request']!=identity(request):raise ValueError('weekly_capture_scope_mismatch')
    return weekly.parse_response(record['body'],request=request,response_environment=request.environment,
        request_started_at=record['request_started_at'],response_received_at=record['response_received_at'],
        source=record['source'],cont_yn=record['cont_yn'],next_key=record['next_key'],response_api_id=record['response_api_id'])


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--stock-code',required=True);p.add_argument('--environment',choices=('mock','real'),required=True)
    p.add_argument('--base-date',required=True);p.add_argument('--adjustment',choices=('0','1'),required=True)
    p.add_argument('--daily-input',type=Path);p.add_argument('--weekly-review-json',type=Path)
    p.add_argument('--output-dir',type=Path);p.add_argument('--max-pages',type=int,default=2)
    p.add_argument('--max-seconds',type=float,default=60)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--live',action='store_true');mode.add_argument('--replay-json',type=Path)
    mode.add_argument('--saved-weekly-json',type=Path)
    p.add_argument('--execute-approved',action='store_true');p.add_argument('--quota-reviewed',action='store_true')
    p.add_argument('--credentials-env-file',type=Path)
    args=p.parse_args(argv);transport=None
    try:
        request=weekly.WeeklyRequest('ka10082',args.stock_code,args.environment,args.base_date,args.adjustment)
        if not 1<=args.max_pages<=20 or not 0<args.max_seconds<=300:raise ValueError('weekly_budget_invalid')
        if not (args.live or args.replay_json or args.saved_weekly_json):
            print(json.dumps(dict(mode='plan_only',request=identity(request),path='/api/dostk/chart',
                max_weekly_requests=args.max_pages,api_calls=0,oauth_calls=0,output_created=False)))
            return 0
        if not args.daily_input or not args.output_dir:raise ValueError('daily_input_and_new_output_required')
        if args.live and (not args.execute_approved or not args.quota_reviewed or not args.credentials_env_file):
            raise ValueError('weekly_live_approval_required')
        if args.live and datetime.strptime(args.base_date,'%Y%m%d').date()>datetime.now(KST).date():
            raise ValueError('future_weekly_base_date')
        document,daily_provenance=read_json(args.daily_input)
        analyze_context(document)  # validate before constructing any HTTP client
        origin='synthetic_fixture' if args.replay_json else 'api_response'
        if args.saved_weekly_json:
            capture,capture_provenance=read_json(args.saved_weekly_json)
            if capture.get('schema_version')!='weekly-capture-v1':raise ValueError('weekly_capture_version_required')
            if capture.get('collection_status') not in ('failed','collected_unverified'):raise ValueError('weekly_capture_status_invalid')
            origin=capture['data_origin']
        if (document['stock_code']!=request.stock_code or document['environment']!=request.environment
                or document['data_origin']!=origin or document['market']!=request.market
                or (document.get('price_basis') or {}).get('mode')!={'0':'original','1':'adjusted'}[request.adjustment]):
            raise ValueError('weekly_daily_input_scope_mismatch')
        review=read_json(args.weekly_review_json)[0] if args.weekly_review_json else {}
        if not isinstance(review,dict):raise ValueError('weekly_review_object_required')
        if review and review.get('scope')!={**identity(request),'data_origin':origin}:raise ValueError('weekly_review_scope_mismatch')
        folder=output_root(args.output_dir)
        if folder.exists() or any(x.name in ('data','.git','token-cache') for x in (folder,*folder.parents)):
            raise ValueError('new_independent_weekly_output_required')
        if args.live:
            token=os.environ.get('KIWOOM_ACCESS_TOKEN');expiry=os.environ.get('KIWOOM_ACCESS_TOKEN_EXPIRES_AT')
            if not token or not expiry:raise ValueError('existing_token_and_expiry_required')
            transport=WeeklyHTTPTransport(credentials(args.credentials_env_file,environment=args.environment),
                request,token,expiry,approved=True)
        elif args.replay_json:transport=ReplayTransport(read_json(args.replay_json)[0])
        if args.saved_weekly_json:
            pages=tuple(restore_page(item,request) for item in capture['pages'])
            if capture.get('page_fingerprints')!=[x.fingerprint for x in pages]:raise ValueError('weekly_capture_fingerprint_mismatch')
            collection=dict(status=capture['collection_status'],reason=capture.get('collection_reason'),
                api_calls=0,oauth_calls=0,attempts=0,data_origin=origin)
            records=[page_record(page,Redactor()) for page in pages]
            pages=tuple(restore_page(item,request) for item in records)
        else:
            collected=weekly.fetch_pages(request,transport,max_pages=args.max_pages,
                max_seconds=args.max_seconds,allow_live=args.live)
            collection={k:v for k,v in collected.items() if k!='pages'}
            records=[page_record(page,transport.redactor) for page in collected['pages']]
            pages=tuple(restore_page(item,request) for item in records)
        capture_out=dict(schema_version='weekly-capture-v1',data_origin=origin,pages=records,
            page_fingerprints=[x.fingerprint for x in pages],collection_status=collection['status'],
            collection_reason=collection['reason'])
        folder.mkdir(parents=True,exist_ok=False)
        with (folder/'weekly-capture.json').open('x',encoding='utf-8') as target:
            json.dump(capture_out,target,ensure_ascii=False,indent=2,allow_nan=False)
        # A failed collection never produces a positive high from partial pages.
        usable=pages if collection['status']=='collected_unverified' else ()
        active_review=review if collection['status']=='collected_unverified' else {}
        if active_review and sorted(active_review['page_fingerprints'])!=sorted(x.fingerprint for x in pages):
            raise ValueError('weekly_review_page_binding_mismatch')
        report=analyze_hybrid(document,usable,weekly_data_origin=origin,
            weekly_price_basis=active_review.get('price_basis'),weekly_calendar=active_review.get('calendar'),
            evidence=[WeeklyEvidence(**value) for value in active_review.get('evidence',())])
        with (folder/'hybrid.json').open('x',encoding='utf-8') as target:
            json.dump(report,target,ensure_ascii=False,indent=2,allow_nan=False)
        summary=dict(module='weekly_probe',mode='live' if args.live else 'saved' if args.saved_weekly_json else 'replay',
            request=identity(request),daily_input=daily_provenance,
            saved_capture=capture_provenance if args.saved_weekly_json else None,collection=collection,
            parsed_rows=sum(len(page.rows) for page in pages),
            verified_completed_weeks=52 if report['primary_high52']['status']=='available' else 0,
            high52_status=report['primary_high52']['status'],
            live_requests_attempted=bool(args.live and collection['api_calls']),
            api_response_reviewed=origin=='api_response' and report['primary_high52']['status']=='available')
        with (folder/'summary.json').open('x',encoding='utf-8') as target:
            json.dump(summary,target,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps(summary,ensure_ascii=False))
        return 2 if collection['status']=='failed' else 0
    except (OSError,ValueError,KeyError,TypeError,TokenManagementError):
        p.error('Invalid weekly input/review/output or approved existing token unavailable; no OAuth or retry')
    finally:
        if transport:transport.close()


if __name__=='__main__':raise SystemExit(main())
