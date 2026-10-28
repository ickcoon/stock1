"""Step 7-B2-A: standalone one-page daily-chart probe. No database access.

Default CLI is a credential-free request plan. --real enables actual HTTP to
--environment (NOT necessarily production); OAuth additionally requires
--authenticate. No retries, token refresh, fallback, continuation or scheduler.
"""
from datetime import datetime, timedelta
import argparse
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4

import requests

from .client import KiwoomClient, KST
from .config import PROJECT_ROOT, load_credentials
from . import daily_chart as daily
from . import daily_tracking_adapter as adapter
from .errors import KiwoomError

DEFAULT_CODES = ('005930', '000660')
DEFAULT_OUTPUT = PROJECT_ROOT / 'diagnostics' / 'daily_chart'
SPEC_REVIEW = {
    'ka10081': 'official GitHub 953e5db + B1 portal record; portal recheck 403 on 2026-10-28',
    'ka11081': 'B1 official portal record only; absent from official GitHub JSON; portal recheck 403 on 2026-10-28',
}
ROW_FIELDS = ('stk_cd', 'dt', 'open_pric', 'high_pric', 'low_pric', 'cur_prc',
              'trde_qty', 'trde_prica', 'pred_pre', 'pred_pre_sig', 'trde_tern_rt',
              'upd_stkpc_tp', 'upd_rt')
SENSITIVE = re.compile(r'(?i)token|secret|app.?key|authorization|bearer|password|계좌|account|cookie')
OPAQUE = re.compile(r'[A-Za-z0-9_+/=.-]{40,}')


def now():
    return datetime.now(KST)


class Redactor:
    def __init__(self, *secrets):
        self.secrets = [s for s in secrets if isinstance(s, str) and s]

    def text(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, '[REDACTED]')
        return text

    def message(self, value):
        if not isinstance(value, str):
            return None
        value = self.text(value)
        if SENSITIVE.search(value):
            return '[REDACTED: sensitive server message]'
        return OPAQUE.sub('[REDACTED]', re.sub(r'\b[0-9]{8,16}\b', '[REDACTED]', value[:1000]))

    def scalar(self, value):
        # Unexpected containers may contain credentials. Retain their type, not content.
        if isinstance(value, str):
            value = self.text(value)
            return '[REDACTED]' if SENSITIVE.search(value) or OPAQUE.search(value) else value
        if type(value) is float and not math.isfinite(value):
            return {'invalid_numeric': 'nonfinite'}
        if value is None or type(value) in (bool, int, float):
            return value
        return {'redacted_container_type': type(value).__name__}

    def body(self, body):
        if not isinstance(body, dict):
            return None
        safe = {key: self.scalar(body[key]) for key in ('stk_cd', 'return_code') if key in body}
        if 'return_msg' in body:
            safe['return_msg'] = self.message(body['return_msg'])
        for key in daily.LIST_KEYS.values():
            if key in body:
                value = body[key]
                safe[key] = [{k: self.scalar(row[k]) for k in ROW_FIELDS if k in row}
                             if isinstance(row, dict) else self.scalar(row) for row in value] if isinstance(value, list) else self.scalar(value)
        return safe


def result_code(body):
    value = body.get('return_code') if isinstance(body, dict) else None
    if type(value) in (int, str) and re.fullmatch(r'-?[0-9]{1,9}', str(value)):
        return value
    return None


def review_tracking(page, *, snapshot=None, calendar=None, as_of=None):
    """Reuse the adapter only with caller-supplied D0/calendar; never invent proofs."""
    if snapshot is None or calendar is None:
        return {'status': 'not_run_missing_d0_or_calendar', 'observations_generated': 0,
                'required_evidence_fields': list(adapter.ClosingEvidence.__dataclass_fields__),
                'reason': 'No D0 snapshot/calendar/closing proof is manufactured by this tool.'}
    converted = adapter.to_tracking((page,), snapshot, calendar=calendar,
        as_of=as_of or page.response_received_at,
        expected_session_basis='regular_session' if page.request.api_id == 'ka11081' else 'chart_session',
        evidence=())
    return {'status': 'held_without_closing_evidence', 'observations_generated': len(converted.observations),
            'holds': [{'page': p, 'row': r, 'reasons': list(why)} for p, r, why in converted.holds]}


def inspect_response(body, *, request, started_at, received_at, http_status,
                     headers, redactor=None):
    """Inspect safe documented-field projection with the unchanged B1 parser."""
    redactor = redactor or Redactor()
    safe = redactor.body(body)
    original_headers = {k.lower():v for k,v in headers.items() if k.lower() in ('api-id','cont-yn','next-key')}
    header_values = {k: redactor.scalar(v) for k, v in original_headers.items()}
    # Case-insensitive lookup also works with plain offline fixture dictionaries.
    lower = {k.lower(): v for k, v in header_values.items()}
    report = {'http_status': http_status, 'return_code': result_code(safe),
              'return_msg': safe.get('return_msg') if safe else None,
              'response_headers': lower, 'response_projection': safe,
              'status': 'parser_error', 'b1_parse_success': False, 'b1_validated': False,
              'projection_policy': 'documented_fields_only; unknown fields and all sensitive headers omitted',
              'tracking': {'status':'not_run_parser_failed', 'observations_generated':0}}
    if http_status != 200:
        report['status'], report['reason'] = 'http_error', 'HTTP response was not 200; no retry.'
        return report
    if report['return_code'] not in (0, '0'):
        report['status'], report['reason'] = 'api_error', 'Missing/invalid/nonzero API return code; no fallback or token refresh.'
        return report
    try:
        page = daily.parse_response(safe, request=request, response_environment=request.environment,
            request_started_at=started_at, response_received_at=received_at,
            source='step7-b2-a documented-field diagnostic projection', http_status=http_status,
            cont_yn=original_headers.get('cont-yn'), next_key=original_headers.get('next-key'), response_api_id=original_headers.get('api-id'))
    except (ValueError, TypeError, OverflowError) as exc:
        # Never echo untrusted response values from exception strings.
        safe_reasons = {'Valid actual receipt interval and source required', 'Mixed mock/real response',
                        'Unsuccessful HTTP response', 'Missing/failed API return code',
                        'Exact response stock code mismatch/unverified', 'Response API mismatch',
                        'Expected API-specific daily row array', 'Daily row object required'}
        report['reason'] = str(exc) if str(exc) in safe_reasons else 'B1 structural validation failed; unsafe exception value omitted.'
        return report
    rows = []
    for row in page.rows:
        raw = row.raw
        rows.append({'index':row.index, 'trade_date':row.trade_date.isoformat() if row.trade_date else None,
            'raw':raw, 'ohlc':{k:str(getattr(row,k)) if getattr(row,k) is not None else None for k in daily.PRICE_FIELDS},
            'close_status':'cur_prc_candidate_not_verified_final_close',
            'volume':str(row.volume) if row.volume is not None else None,
            'turnover_raw':str(row.turnover_raw) if row.turnover_raw is not None else None,
            'turnover_won':str(row.turnover_won) if row.turnover_won is not None else None,
            'price_signs':{k:(v[:1] if isinstance(v,str) and v[:1] in ('+','-') else '')
                           for k in daily.PRICE_FIELDS.values() for v in (raw.get(k),)},
            'issues':list(row.issues), 'eligible_for_tracking':False})
    valid = not page.issues and all(not row.issues for row in page.rows)
    report.update(b1_parse_success=True, b1_validated=valid, rows=rows,
        status='parsed' if valid and rows else 'empty' if not rows else 'parsed_with_issues',
        ordering=page.ordering, page_issues=list(page.issues), units=page.units,
        pagination=page.pagination, continuation_followed=False,
        tracking=review_tracking(page), adjustment_request=request.adjustment,
        adjustment_note='Request 0/1 is distinct from row corporate-action upd_stkpc_tp/upd_rt. No re-adjustment.')
    return report


class _OneShotSession:
    def __init__(self, transport, owner):
        self.transport, self.owner = transport, owner

    def close(self):
        self.transport.close()

    def post(self, url, **kwargs):
        owner = self.owner
        if owner.real is not True:
            raise KiwoomError('Explicit --real required; HTTP blocked.')
        headers, body = kwargs.get('headers',{}), kwargs.get('json',{})
        if kwargs.get('allow_redirects') is not False:
            raise KiwoomError('Redirects are prohibited.')
        if url == owner.credentials.base_url+'/oauth2/token':
            if (not owner.allow_auth or owner.counts['oauth'] >= 1
                    or body != {'grant_type':'client_credentials','appkey':owner.credentials.app_key,'secretkey':owner.credentials.secret_key}):
                raise KiwoomError('OAuth not authorized or already attempted.')
            kind = 'oauth'
        elif url == owner.credentials.base_url+daily.API_PATH:
            key = (headers.get('api-id'), body.get('stk_cd'), body.get('base_dt'), body.get('upd_stkpc_tp'))
            if set(body) != {'stk_cd','base_dt','upd_stkpc_tp'} or key not in owner.allowed or key in owner.attempted:
                raise KiwoomError('Unapproved or repeated daily request blocked.')
            owner.attempted.add(key)
            kind = 'daily'
        else:
            raise KiwoomError('Only OAuth and the approved daily-chart endpoint are allowed.')
        owner.counts[kind] += 1  # Count attempted sends, including transmission-unknown failures.
        return self.transport.post(url, **kwargs)


class DailyDiagnosticClient(KiwoomClient):
    def __init__(self, credentials, plans, *, real=False, allow_auth=False, access_token=None, clock=now):
        if real is not True:
            raise ValueError('Explicit --real required to construct the HTTP probe.')
        plans = tuple(plans)
        if not 1 <= len(plans) <= 2 or len({r.stock_code for r in plans}) != len(plans):
            raise ValueError('One or two distinct stocks only.')
        if any(r.environment != credentials.environment for r in plans):
            raise ValueError('Requested environment must match .env KIWOOM_ENV.')
        if credentials.base_url != {'mock':'https://mockapi.kiwoom.com','real':'https://api.kiwoom.com'}[credentials.environment]:
            raise ValueError('Unapproved API host.')
        if any(daily.day(r.base_dt) >= clock().astimezone(KST).date() for r in plans):
            raise ValueError('Live probes require explicit historical base dates.')
        super().__init__(credentials)
        self.real, self.allow_auth, self.clock = real, allow_auth, clock
        self.allowed = {(r.api_id,r.stock_code,r.base_dt,r.adjustment) for r in plans}
        self.attempted, self.counts = set(), {'oauth':0,'daily':0}
        self.redactor = Redactor(credentials.app_key,credentials.secret_key,access_token)
        self.access_token = access_token
        self.auth_diagnostic = {'status':'not_attempted'}
        self.session = _OneShotSession(self.session,self)

    def _post(self, path, body, headers, *, attempts=1):
        # authenticate() is reused, but all retry logic is replaced by a single paced send.
        if path != '/oauth2/token':
            raise KiwoomError('Only explicit OAuth may use this path.')
        try:
            response = self._request(path,body,headers)
        except requests.RequestException:
            self.auth_diagnostic = {'status':'transport_error','reason':'OAuth transport failed; no retry.'}
            raise KiwoomError('OAuth transport failed; no retry.') from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload,dict) and isinstance(payload.get('token'),str):
            self.redactor.secrets.append(payload['token'])
        self.auth_diagnostic = {'status':'response_received','http_status':response.status_code,
            'return_code':result_code(payload),
            'return_msg':self.redactor.message(payload.get('return_msg')) if isinstance(payload,dict) else None}
        return response,payload

    def prepare_auth(self):
        if self.access_token:
            self.auth_diagnostic = {'status':'provided_environment_token','expiry':'unknown; server validates once; no refresh'}
            return True
        if self.token is not None and self.token.expires_at > self.clock()+timedelta(seconds=30):
            self.access_token = self.token.value
            self.redactor.secrets.append(self.access_token)
            self.auth_diagnostic = {'status':'existing_in_memory_token'}
            return True
        if not self.allow_auth:
            self.auth_diagnostic = {'status':'blocked','reason':'Set KIWOOM_ACCESS_TOKEN or explicitly add --authenticate. No automatic OAuth.'}
            return False
        try:
            token = self.authenticate(attempts=1)
        except KiwoomError:
            self.auth_diagnostic['status'] = 'authentication_failed'
            self.auth_diagnostic.setdefault('reason','OAuth rejected or malformed; inspect actual HTTP/return_code. No retry.')
            return False
        self.access_token = token.value
        self.redactor.secrets.append(token.value)
        self.auth_diagnostic['status'] = 'authenticated'
        return True

    def probe(self, request):
        if not self.access_token:
            raise ValueError('Explicit authentication preparation required.')
        result = {'request':{'api_id':request.api_id,'body':request.body}, 'environment':request.environment,
                  'request_started_at':None,'response_received_at':None,'request_finished_at':None}
        key = (request.api_id,request.stock_code,request.base_dt,request.adjustment)
        if not self.real or request.environment != self.credentials.environment or key not in self.allowed or key in self.attempted:
            result.update(status='request_blocked',reason='Unapproved environment/identity or repeated stock; no HTTP sent.')
            return result
        def before_send():
            result['request_started_at'] = self.clock().astimezone(KST).isoformat()
        try:
            response = self._request(daily.API_PATH,request.body,
                {'api-id':request.api_id,'authorization':'Bearer '+self.access_token},before_send=before_send)
        except (requests.RequestException, KiwoomError) as exc:
            result.update(status='transport_error' if isinstance(exc,requests.RequestException) else 'request_blocked',
                          reason='HTTP transport failed or request guard blocked; no retry. Exception text omitted.')
            return result
        finally:
            result['request_finished_at'] = self.clock().astimezone(KST).isoformat()
        result['response_received_at'] = result['request_finished_at']
        try:
            body = response.json()
        except ValueError:
            result.update(status='invalid_json',http_status=response.status_code,reason='Non-JSON response; body not retained.')
            return result
        result.update(inspect_response(body,request=request,started_at=result['request_started_at'],
            received_at=result['response_received_at'],http_status=response.status_code,
            headers=response.headers,redactor=self.redactor))
        return result


def output_root(path):
    supplied = Path(path)
    if supplied.is_symlink():
        raise ValueError('Symlink output directories are not allowed.')
    target = supplied.resolve()
    project = PROJECT_ROOT.resolve()
    data_root = project/'data'
    if target == data_root or data_root in target.parents:
        raise ValueError('Operating data directories cannot hold diagnostics.')
    if (project/'diagnostics').is_symlink() or DEFAULT_OUTPUT.is_symlink():
        raise ValueError('Diagnostic output root must not be a symlink.')
    # Never place diagnostics in data/, source, or any other project subtree.
    if target == project or project in target.parents:
        if target != DEFAULT_OUTPUT.resolve() and DEFAULT_OUTPUT.resolve() not in target.parents:
            raise ValueError('Inside the project use diagnostics/daily_chart only; never an operating DB/data path.')
    return target


def save_report(folder, report):
    path = folder/'report.json'
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,allow_nan=False)
    return path


def execute(plans, client, folder):
    report = {'version':'step7-b2-a-v1','mode':'live_http_diagnostic','environment':client.credentials.environment,
              'data_origin':'mock_server_unverified_source' if client.credentials.environment=='mock' else 'real_server_unverified_daily_bars',
              'database_access':False,'results':[], 'spec_review':dict(SPEC_REVIEW)}
    authorized = client.prepare_auth()
    failed = not authorized
    for request in plans:
        if failed:
            report['results'].append({'request':{'api_id':request.api_id,'body':request.body},
                'status':'not_attempted','reason':'Authentication or a previous request failed; no automatic continuation.'})
            continue
        item = client.probe(request)
        report['results'].append(item)
        failed = item['status'] not in ('parsed','empty')
    report.update(auth=client.auth_diagnostic,request_counts=dict(client.counts),status='error' if failed else 'completed')
    path = save_report(folder,report)
    return report,path


def main(argv=None):
    parser = argparse.ArgumentParser(description='Standalone daily-chart probe: default plan only, no DB or HTTP.')
    parser.add_argument('--real',action='store_true',help='Explicitly allow live HTTP to --environment; NOT a real-server selector')
    parser.add_argument('--authenticate',action='store_true',help='With --real: allow at most one OAuth request if no supplied token')
    parser.add_argument('--environment',choices=('mock','real'),default='mock')
    parser.add_argument('--api-id',choices=tuple(daily.LIST_KEYS),default='ka10081')
    parser.add_argument('--stock-code',action='append',help='Repeat for up to two distinct stocks; default 005930 and 000660')
    parser.add_argument('--base-date',help='Explicit historical base date YYYYMMDD; required with --real')
    parser.add_argument('--adjustment',choices=('0','1'),default='0')
    parser.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    codes = args.stock_code if args.stock_code is not None else list(DEFAULT_CODES)
    if not 1 <= len(codes) <= 2 or len(set(codes)) != len(codes):
        parser.error('Use one or two distinct --stock-code values.')
    if args.authenticate and not args.real:
        parser.error('--authenticate requires --real.')
    plans = []
    stage = 'request_validation'
    try:
        for code in codes:
            if not daily.CODE.fullmatch(code) or args.environment == 'mock' and code.endswith(('_NX','_AL')):
                raise ValueError('Invalid explicit stock/market context.')
            if args.base_date is not None:
                plans.append(daily.DailyRequest(args.api_id,code,args.environment,args.base_date,args.adjustment))
        if not args.real:
            print(json.dumps({'mode':'plan_no_http','database_access':False,'credentials_loaded':False,
                'environment':args.environment,'maximum_daily_requests':len(codes),'oauth_requests':0,'spec_review':dict(SPEC_REVIEW),
                'requests':[{'api_id':args.api_id,'path':daily.API_PATH,'body':dict(stk_cd=code,base_dt=args.base_date,upd_stkpc_tp=args.adjustment)} for code in codes],
                'required_for_live':'--real and explicit past --base-date; .env environment match; token or --authenticate',
                'output_dir':str(output_root(args.output_dir)),'files_written':False},ensure_ascii=False,indent=2))
            return 0
        stage = 'historical_date_guard'
        if not args.base_date or daily.day(args.base_date) >= now().date():
            raise ValueError('--real requires an explicit past --base-date. No calendar date is guessed.')
        stage = 'output_validation'
        root = output_root(args.output_dir)
        stage = 'credential_configuration'
        credentials = load_credentials()
        stage = 'environment_match'
        if credentials.environment != args.environment:
            raise ValueError('--environment and .env KIWOOM_ENV must match; no automatic server switch.')
        stage = 'output_creation'
        folder = root/args.environment/(now().strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:12])
        folder.mkdir(parents=True,mode=0o700,exist_ok=False)
        stage = 'client_initialization'
        client = DailyDiagnosticClient(credentials,plans,real=args.real,allow_auth=args.authenticate,
                                       access_token=os.environ.get('KIWOOM_ACCESS_TOKEN'))
        try:
            stage = 'probe_execution'
            report,path = execute(plans,client,folder)
            print(json.dumps(dict(report,output=str(path)),ensure_ascii=False,indent=2))
            return 1 if report['status']=='error' else 0
        finally:
            client.close()
    except (ValueError, KiwoomError):
        # Credential/config/transport exception values are never printed.
        print(json.dumps({'status':'blocked','stage':stage,'reason':'Check request syntax, explicit past date, permitted output directory, .env credentials and environment match. Unsafe exception values are omitted.'}))
        return 1
    except OSError:
        print(json.dumps({'status':'output_error','reason':'Diagnostic output path unavailable; no operating DB fallback.'}))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({'status':'interrupted','reason':'No retry or resumption. No OAuth token retained.'}))
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
