"""ka10082 adapter: reuse daily field validation, never label weekly bars as daily.

Same chart endpoint/body/continuation headers as ka10081; a different row array.
Official weekly trde_prica is KRW, unlike daily million_KRW. No live transport
guard is widened here. Supply's ReplayTransport implements the fetch protocol.
"""
from copy import deepcopy
from dataclasses import dataclass,replace
from datetime import datetime
import time

from . import daily_chart as daily
from .leader_tracking import canonical

LIST_KEY='stk_stk_pole_chart_qry'


@dataclass(frozen=True)
class WeeklyRequest(daily.DailyRequest):
    def __post_init__(self):
        if self.api_id!='ka10082':raise ValueError('explicit_weekly_api_required')
        daily.DailyRequest('ka10081',self.stock_code,self.environment,self.base_dt,self.adjustment)


@dataclass(frozen=True)
class WeeklyPage:
    request: WeeklyRequest
    request_started_at: datetime
    response_received_at: datetime
    raw_json: str
    rows: tuple[daily.DailyRow,...]
    cont_yn: str | None
    next_key: str | None
    response_api_id: str | None
    ordering: str
    issues: tuple
    source: str

    @property
    def raw(self):
        import json
        return json.loads(self.raw_json)

    @property
    def fingerprint(self):
        return daily.fingerprint(['weekly-v1',self.request.api_id,self.request.environment,self.request.body,
            self.request_started_at.isoformat(),self.response_received_at.isoformat(),self.raw_json,
            self.cont_yn,self.next_key,self.response_api_id,self.source])

    @property
    def units(self):return dict(price='KRW',volume='share',turnover='KRW',status='documented_not_runtime_verified')


def parse_response(body,*,request,response_environment,request_started_at,response_received_at,
                   source,http_status=200,cont_yn=None,next_key=None,response_api_id=None):
    if not isinstance(request,WeeklyRequest):raise ValueError('weekly_request_required')
    if response_api_id not in (None,'ka10082'):raise ValueError('weekly_response_api_mismatch')
    if not isinstance(body,dict) or LIST_KEY not in body or daily.LIST_KEYS['ka10081'] in body:
        raise ValueError('weekly_row_array_required')
    # Internal projection only: reuse OHLC/date/volume validation without changing
    # any daily registry. Returned raw data/fingerprints/API always remain weekly.
    projected=deepcopy(body);projected[daily.LIST_KEYS['ka10081']]=projected.pop(LIST_KEY)
    normalized=daily.parse_response(projected,request=daily.DailyRequest('ka10081',request.stock_code,
        request.environment,request.base_dt,request.adjustment),response_environment=response_environment,
        request_started_at=request_started_at,response_received_at=response_received_at,source=source,
        http_status=http_status,cont_yn=cont_yn,next_key=next_key,
        response_api_id='ka10081' if response_api_id else None)
    rows=tuple(replace(r,turnover_won=r.turnover_raw) for r in normalized.rows)
    return WeeklyPage(request,normalized.request_started_at,normalized.response_received_at,canonical(body),
        rows,cont_yn,next_key,response_api_id,normalized.ordering,normalized.issues,source)


def fetch_pages(request,transport,*,max_pages=3,max_seconds=60,target_bars=53,allow_live=False):
    """Bounded injected transport; no auth, refresh or retries. 53 allows a live week.

    Structural row count never proves 52 completed weeks. The hybrid review gate
    makes that decision. Default invocation rejects any non-fixture transport.
    """
    if not isinstance(request,WeeklyRequest) or not 1<=max_pages<=20 or not 0<max_seconds<=300 or not 53<=target_bars<=200:
        raise ValueError('weekly_budget_or_request_invalid')
    if transport.origin not in ('synthetic_fixture','api_response') or (transport.origin=='api_response' and not allow_live):
        raise ValueError('weekly_live_not_authorized')
    deadline=time.perf_counter()+max_seconds;pages=[];seen=set();dates=set();previous=None;continuation={}
    before=transport.calls;reason=None
    try:
        for _ in range(max_pages):
            if time.perf_counter()>=deadline:raise ValueError('weekly_time_budget')
            reply=transport.fetch(request,continuation,deadline)
            if time.perf_counter()>deadline:raise ValueError('weekly_time_budget')
            raw=reply['body'];projected=deepcopy(raw)
            if isinstance(raw,dict) and LIST_KEY in raw:
                safe=transport.redactor.body({**raw,daily.LIST_KEYS['ka10081']:raw[LIST_KEY]})
                safe[LIST_KEY]=safe.pop(daily.LIST_KEYS['ka10081'])
                safe.pop('return_msg',None);projected=safe
            headers={k.lower():v for k,v in reply['headers'].items()}
            page=parse_response(projected,request=request,response_environment=request.environment,
                request_started_at=reply['request_started_at'],response_received_at=reply['response_received_at'],
                source='weekly documented-field projection',http_status=reply['http_status'],
                cont_yn=headers.get('cont-yn'),next_key=headers.get('next-key'),response_api_id=headers.get('api-id'))
            if page.ordering=='unordered' or any(p!='daily_order_unverified' for p in page.issues):
                raise ValueError('weekly_page_quality')
            current={r.trade_date for r in page.rows if r.trade_date}
            if current and previous and min(current)>=previous:raise ValueError('weekly_no_progress')
            if current:previous=min(current)
            pages.append(page);dates.update(current)
            if len(dates)>=target_bars or page.cont_yn=='N':break
            cursor=page.next_key
            if page.cont_yn!='Y' or not isinstance(cursor,str) or not 0<len(cursor)<=50 or '\r' in cursor or '\n' in cursor:
                raise ValueError('weekly_continuation_unknown')
            if cursor in seen:raise ValueError('weekly_repeated_cursor')
            seen.add(cursor);continuation={'cont-yn':'Y','next-key':cursor}
        else:raise ValueError('weekly_page_budget')
    except Exception as exc:
        allowed={'weekly_time_budget','weekly_page_quality','weekly_no_progress',
                 'weekly_continuation_unknown','weekly_repeated_cursor','weekly_page_budget'}
        reason=str(exc) if type(exc) is ValueError and str(exc) in allowed else 'weekly_transport_or_parser_failure'
    return dict(status='failed' if reason else 'collected_unverified',reason=reason,pages=tuple(pages),
        data_origin=transport.origin,attempts=transport.calls-before,
        api_calls=transport.calls-before if transport.origin=='api_response' else 0,oauth_calls=0,
        verified_completed_weeks=0)
