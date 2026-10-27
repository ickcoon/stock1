"""Independent ka10001 offline contract, parsing and evidence-gated eligibility.

No credentials, DB, OAuth, requests or live network client. Fixture-only CLI.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re

API_ID='ka10001'
API_PATH='/api/dostk/stkinfo'
SPEC_URL='https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json'
CODE=re.compile(r'[0-9A-Z]{6}(?:_NX|_AL)?',re.ASCII)
NUM=re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?',re.ASCII)
FIELDS=('stk_cd','stk_nm','mac','flo_stk','cur_prc')
KST=timezone(timedelta(hours=9))


def instant(value):
    try:
        result=datetime.fromisoformat(value)
        return result.astimezone(KST) if result.tzinfo else None
    except (TypeError,ValueError):return None


def numeric(value):
    if not isinstance(value,str) or len(value)>20 or not NUM.fullmatch(value):return None
    return Decimal(value)


def validate_request(code,environment):
    if environment not in ('mock','real'):raise ValueError('Explicit mock or real required')
    if not isinstance(code,str) or CODE.fullmatch(code) is None:raise ValueError('Exact string code required')
    if environment=='mock' and code.endswith(('_NX','_AL')):
        raise ValueError('Mock test restricted to KRX; NXT/SOR support not verified')
    return 'NXT' if code.endswith('_NX') else 'SOR' if code.endswith('_AL') else 'KRX'


def request_plan(codes,environment='mock'):
    unique=[];duplicates=[]
    for code in codes:
        validate_request(code,environment)
        if code in unique:duplicates.append(code)
        else:unique.append(code)
    if not 1<=len(unique)<=10:raise ValueError('Independent plan requires 1..10 unique codes')
    return dict(api_id=API_ID,environment=environment,
        host='https://mockapi.kiwoom.com' if environment=='mock' else 'https://api.kiwoom.com',
        method='POST',path=API_PATH,requests=[{'stk_cd':c} for c in unique],duplicate_codes=duplicates,
        max_query_attempts=len(unique),max_oauth_attempts=1,planned_http_upper_bound=len(unique)+1,
        request_interval_seconds=1.2,retries=0,pagination=False,live_execution_implemented=False,
        actual_api_calls=0,actual_oauth_calls=0)


def plan_from_cross(report,limit=10):
    if type(limit) is not int or not 1<=limit<=10:raise ValueError('Limit must be 1..10')
    env=report['sources']['ranking']['environment']
    if env!=report['sources']['turnover']['environment']:raise ValueError('Mixed environments forbidden')
    rows=[r for r in report['rows'] if r.get('filter_selected') and type(r.get('turnover_rank')) is int and 1<=r['turnover_rank']<=40
          and r.get('security_type') not in ('ETF','ETN','preferred_stock','SPAC','REIT','other')]
    rows.sort(key=lambda r:(r.get('ranking_0198',99)>5,-r.get('top5_appearances',0),r['turnover_rank'],r['stock_code']))
    codes=list(dict.fromkeys(r['stock_code'] for r in rows))[:limit]
    if not codes:return dict(environment=env,requests=[],actual_api_calls=0,actual_oauth_calls=0,reason='no_eligible_observation_targets')
    return request_plan(codes,env)


def parse_response(body,*,requested_code,environment,request_started_at,response_received_at,
                   http_status=200,cont_yn='N',security_type='unknown',security_type_evidence=None):
    venue=validate_request(requested_code,environment)
    issues=[];raw={}
    start,end=instant(request_started_at),instant(response_received_at)
    if not start or not end or end<start or start.date()!=end.date():issues.append('invalid_observation_interval')
    if type(http_status) is not int or not 200<=http_status<300:
        issues.append('rate_limited' if http_status==429 else 'http_failed')
    code=None
    if not isinstance(body,dict):issues.append('invalid_response_object')
    else:
        value=body.get('return_code')
        if type(value) is int and abs(value)<=2147483647:code=value
        elif isinstance(value,str) and re.fullmatch(r'-?[0-9]{1,10}',value):code=int(value)
        if code!=0:issues.append('api_return_code_missing_or_failed')
        for field in FIELDS:
            value=body.get(field)
            if value is None or isinstance(value,str) and not value.strip():issues.append('missing:'+field)
            elif not isinstance(value,str) or len(value)>(40 if field=='stk_nm' else 20):issues.append('invalid_type_or_length:'+field)
            else:raw[field]=value
    if raw.get('stk_cd')!=requested_code:issues.append('response_code_mismatch')
    mac,shares,price=(numeric(raw.get(f)) for f in ('mac','flo_stk','cur_prc'))
    for field,value in (('mac',mac),('flo_stk',shares),('cur_prc',price)):
        if value is None:issues.append('invalid_numeric:'+field)
    if mac is not None and raw['mac'].startswith('-'):issues.append('negative_market_cap')
    if shares is not None and raw['flo_stk'].startswith('-'):issues.append('negative_listed_stock_field')
    if cont_yn not in (None,'','N'):issues.append('unexpected_continuation_no_followup')
    known_type=security_type if security_type_evidence and security_type in ('common_stock','preferred_stock','ETF','ETN','SPAC','REIT','other') else 'unknown'
    with localcontext() as ctx:
        ctx.prec=80
        won=format(mac*Decimal(100000000),'f') if mac is not None and mac>=0 and not raw['mac'].startswith('-') else None
    key=dict(environment=environment,requested_code=requested_code,raw=raw,request_started_at=request_started_at,response_received_at=response_received_at,
             security_type=known_type,security_type_evidence=security_type_evidence if known_type!='unknown' else None)
    fingerprint=hashlib.sha256(json.dumps(key,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return dict(api_id=API_ID,request_path=API_PATH,environment=environment,requested_code=requested_code,venue=venue,
        raw=raw,market_cap_source_unit='hundred_million_KRW',market_cap_won_diagnostic=won,
        listed_stock_unit_documented='thousand_KRW_inconsistent_with_share_count',listed_shares=None,
        listed_stock_unit_status='unverified_spec_conflict',price_signed_diagnostic=str(price) if price is not None else None,
        market_cap_reconstructed_from_price_and_shares=None,mac_unit_runtime_verified=False,market_cap_basis_verified=False,
        request_started_at=request_started_at,response_received_at=response_received_at,
        retrieval_date=start.date().isoformat() if start else None,market_asof=None,trade_date=None,
        security_type=known_type,security_type_evidence=security_type_evidence if known_type!='unknown' else None,
        http_status=http_status,return_code=code,issues=list(dict.fromkeys(issues)),status='invalid' if issues else 'syntax_valid_unverified',
        fingerprint=fingerprint,market_cap_meets_threshold=None,candidate_confirmed=False)


def parse_fixture(document):
    if document.get('api_id')!=API_ID:raise ValueError('ka10001 fixture required')
    env=document['environment'];records=document['records']
    if not isinstance(records,list) or len(records)>10:raise ValueError('At most 10 fixture records')
    results=[parse_response(r.get('body'),requested_code=r['requested_code'],environment=env,
        request_started_at=r.get('request_started_at'),response_received_at=r.get('response_received_at'),
        http_status=r.get('http_status',200),cont_yn=r.get('cont_yn','N'),security_type=r.get('security_type','unknown'),
        security_type_evidence=r.get('security_type_evidence')) for r in records]
    counts=Counter(r['requested_code'] for r in results)
    for r in results:
        if counts[r['requested_code']]>1:
            r['issues'].append('duplicate_code');r['status']='invalid'
    return dict(environment=env,records=results,actual_api_calls=0,actual_oauth_calls=0,candidate_confirmed=False)


class IndependentClient:
    """Injected transport only: no default HTTP/OAuth implementation.

    A future approved launcher must provide TLS/auth/pacing; this boundary enforces
    a max-ten one-shot budget and stops the batch on every failure/continuation.
    """
    def __init__(self,transport=None,*,environment='mock',network_approved=False,max_queries=10):
        if environment not in ('mock','real') or type(max_queries) is not int or not 1<=max_queries<=10:
            raise ValueError('Invalid independent query scope')
        self.transport=transport;self.environment=environment;self.approved=network_approved is True
        self.max_queries=max_queries;self.attempts=0;self.seen=set();self.stopped=False

    def fetch_once(self,code,*,security_type='unknown',security_type_evidence=None):
        validate_request(code,self.environment)
        if not self.approved or not callable(self.transport):raise ValueError('Approved external transport required')
        if self.stopped or code in self.seen or self.attempts>=self.max_queries:raise ValueError('Query budget/replay blocked')
        self.seen.add(code);self.attempts+=1
        try:
            envelope=self.transport(api_id=API_ID,path=API_PATH,body={'stk_cd':code},environment=self.environment)
            if envelope.get('environment')!=self.environment:raise ValueError('Transport environment mismatch')
            result=parse_response(envelope.get('body'),requested_code=code,environment=self.environment,
                request_started_at=envelope.get('request_started_at'),response_received_at=envelope.get('response_received_at'),
                http_status=envelope.get('http_status'),cont_yn=envelope.get('cont_yn','N'),
                security_type=security_type,security_type_evidence=security_type_evidence)
            if result['issues']:self.stopped=True
            return result
        except Exception:
            self.stopped=True
            raise ValueError('Independent query stopped; no retry; original details omitted') from None


def eligibility(cap,turnover,*,at,evidence=None,ranking_0198=None,top5_appearances=0):
    """External evidence is required and bound to this record, never inferred.

    Only numeric limits are fixed. Market timestamps must match exactly; no new
    tolerance or cache TTL silently turns historical data into current evidence.
    """
    if cap['environment']!=turnover.get('environment'):raise ValueError('Mixed mock/real forbidden')
    expected=instant(at);proof=evidence or {}
    key={k:cap.get(k) for k in ('environment','requested_code','raw','request_started_at','response_received_at','security_type','security_type_evidence')}
    fingerprint=hashlib.sha256(json.dumps(key,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    bound=proof.get('for_record')==cap['fingerprint']==fingerprint and proof.get('source') and proof.get('environment')==cap['environment']
    actual=instant(proof.get('market_asof'));cash_at=instant(turnover.get('market_asof'))
    received=instant(cap.get('response_received_at'))
    time_ok=bool(expected and actual and received and actual<=received and actual==expected and cash_at==expected and proof.get('trade_date')==expected.date().isoformat() and turnover.get('trade_date')==expected.date().isoformat())
    reasons=[]
    if cap['issues']:reasons.append('invalid_market_cap_record')
    if cap['security_type']!='common_stock':reasons.append('excluded_security_type' if cap['security_type']!='unknown' else 'security_type_unverified')
    if turnover.get('stock_code')!=cap['requested_code']:reasons.append('turnover_code_mismatch')
    mac_ok=bool(bound and proof.get('mac_unit_runtime_verified') is True and proof.get('market_cap_basis_verified') is True and not cap['issues'] and cap['security_type']=='common_stock' and time_ok)
    if not mac_ok:reasons.append('market_cap_evidence_unverified')
    cash_ok=bool(time_ok and turnover.get('unit_runtime_verified') is True and turnover.get('basis')=='closing_cumulative'
                 and turnover.get('closing_basis_verified') is True and turnover.get('market_conditions_verified') is True
                 and turnover.get('evidence') and turnover.get('stock_code')==cap['requested_code'])
    if not cash_ok:reasons.append('closing_turnover_evidence_unverified')
    if not time_ok:reasons.append('market_time_or_trade_date_unverified')
    mac=numeric(cap.get('market_cap_won_diagnostic'))
    original_mac=numeric(cap.get('raw',{}).get('mac'))
    with localcontext() as ctx:
        ctx.prec=80
        if original_mac is None or mac is None or mac!=original_mac*Decimal(100000000):
            mac_ok=False;reasons.append('market_cap_conversion_unverified')
    cash=numeric(turnover.get('turnover_won'))
    cap_pass=mac>=Decimal(300000000000) if mac_ok and mac is not None and mac>=0 else None
    cash_pass=cash>=Decimal(100000000000) if cash_ok and cash is not None and cash>=0 else None
    cash_rank=turnover.get('turnover_rank')
    rank_pass=(1<=cash_rank<=40) if type(cash_rank) is int else None
    checks=dict(market_cap_3000_eok=cap_pass,closing_turnover_1000_eok=cash_pass,turnover_rank_40=rank_pass)
    if cap_pass is False:reasons.append('market_cap_below_threshold')
    if cash_pass is False:reasons.append('closing_turnover_below_threshold')
    if rank_pass is not True:reasons.append('turnover_rank_outside_or_unknown')
    passed=not reasons and all(v is True for v in checks.values())
    status=('대상 제외' if cap['security_type'] not in ('common_stock','unknown') else
            '필수조건 미충족' if any(v is False for v in checks.values()) else
            '장마감 필수조건 충족' if passed else '검증 보류')
    return dict(checks=checks,status=status,reasons=reasons,
        priority_review=type(ranking_0198) is int and 1<=ranking_0198<=5,top5_appearances=top5_appearances,
        sector_co_rise=turnover.get('sector_co_rise') if turnover.get('sector_co_rise_verified') is True else None,
        observation_scope='closing' if cash_ok else 'intraday_provisional_or_unknown',
        sector_detection_excluded=False,candidate_confirmed=False)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--codes',nargs='+',required=True);plan.add_argument('--environment',choices=('mock','real'),default='mock')
    fixture=sub.add_parser('fixture');fixture.add_argument('--input',type=Path,required=True)
    args=p.parse_args(argv)
    if args.command=='plan':result=request_plan(args.codes,args.environment)
    else:
        if args.input.suffix.lower()!='.json':p.error('Independent JSON fixture only')
        result=parse_fixture(json.loads(args.input.read_text(encoding='utf-8-sig')))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
