"""Prior 52 completed weekly bars + unchanged C14-B daily context.

Call analyze_hybrid(daily_input, weekly_pages, weekly_data_origin=...,
    weekly_price_basis=..., weekly_calendar=..., evidence=[WeeklyEvidence(...)])
No weekly completion/calendar/price-comparability assertion is manufactured.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date,timedelta
from decimal import Decimal,localcontext

from .. import weekly_chart as weekly
from ..daily_chart import fingerprint
from ..leader_tracking import timestamp
from .chart import known
from .context import analyze_context


@dataclass(frozen=True)
class WeeklyEvidence:
    page_fingerprint: str
    row_fingerprint: str
    period_start: str
    period_end: str
    verified_at: str
    available_at: str
    source: str
    basis_id: str
    session_basis: str
    status: str = 'unverified'
    price_unit: str | None = None


def analyze_hybrid(document,pages,*,weekly_data_origin,weekly_price_basis=None,
                   weekly_calendar=None,evidence=(),candidate_context=None,weekly_context=None):
    standalone=document is None
    if standalone:
        context=weekly_context or {}
        daily={**context, 'reference_close':None,
            'status':'unavailable','reason':'daily_input_missing','price_basis':None,
            'moving_averages':{f'MA{n}':dict(status='unavailable',value=None,reason='daily_input_missing')
                               for n in (3,5,20,60,200)},
            'supply_proxy':dict(status='unavailable',reason='daily_input_missing',areas=[])}
    else:
        daily=analyze_context(document,candidate_context=candidate_context)
    cutoff=timestamp(daily['as_of']);basis=weekly_price_basis or {};calendar=weekly_calendar or {}
    pages=tuple(pages);evidence=tuple(evidence)
    reason=None;periods=[];reference=None;held=[];valid={}
    daily_basis={} if standalone else daily['analysis']['evidence']['price_basis'] or {}
    scope_ok=weekly_data_origin==daily['data_origin'] and all(
        p.request.stock_code==daily['stock_code'] and p.request.environment==daily['environment']
        and p.request.market==daily['market'] and p.request.adjustment==
        {'original':'0','adjusted':'1'}.get(basis.get('mode')) for p in pages)
    basis_ok=(known(basis,cutoff) and basis.get('verified') is True and basis.get('comparable') is True
        and basis.get('corporate_actions_checked') is True and bool(basis.get('id'))
        and (standalone or all(basis.get(k)==daily_basis.get(k) for k in ('id','mode','effective_at')))
        and bool(basis.get('session_basis'))
        and (standalone or (basis.get('session_basis')==document.get('session_basis')
            and 'price_adjustment_or_corporate_actions_unverified' not in
            {r['code'] for r in daily['analysis']['risks']})))
    try:basis_ok=basis_ok and timestamp(basis['effective_at'])<=timestamp(basis['available_at'])<=cutoff
    except (KeyError,TypeError,ValueError):basis_ok=False
    if not scope_ok:reason='weekly_daily_scope_mismatch'
    elif not basis_ok:reason='weekly_daily_price_basis_unverified_or_mismatch'
    elif not standalone and not daily['reference_close']:reason='daily_reference_close_unavailable'
    elif not known(calendar,cutoff) or calendar.get('verified') is not True:reason='weekly_calendar_unverified'
    else:
        try:
            if timestamp(calendar['as_of'])!=cutoff:raise ValueError()
            reference=calendar['reference_week']
            start,end=date.fromisoformat(reference['start']),date.fromisoformat(reference['end'])
            close_day=date.fromisoformat(context['reference_date']) if standalone else date.fromisoformat(daily['reference_close']['trade_date'])
            if not start<=close_day<=end or not 0<=(end-start).days<=6:raise ValueError()
            periods=sorted(calendar['completed_weeks'],key=lambda p:p['start'])
            starts=[date.fromisoformat(p['start']) for p in periods]
            if len(starts)!=len(set(starts)):raise ValueError()
            for p,s in zip(periods,starts):
                e=date.fromisoformat(p['end']);bar=date.fromisoformat(p['bar_date'])
                if not s<=bar<=e<start or not 0<=(e-s).days<=6:raise ValueError()
            periods=periods[-52:]
            if len(periods)<52:reason='insufficient_completed_weeks'
            elif [date.fromisoformat(p['start']) for p in periods]!=[start-timedelta(weeks=n) for n in range(52,0,-1)]:
                reason='missing_completed_calendar_week'
        except (KeyError,TypeError,ValueError):reason='weekly_calendar_invalid'
    # Reparse original weekly data to prevent a modified normalized model from
    # bypassing the existing OHLC checks. No global daily API registry is changed.
    rows=[]
    for page in pages:
        parsed=weekly.parse_response(page.raw,request=page.request,response_environment=page.request.environment,
            request_started_at=page.request_started_at,response_received_at=page.response_received_at,
            source=page.source,cont_yn=page.cont_yn,next_key=page.next_key,response_api_id=page.response_api_id)
        if parsed!=page:raise ValueError('weekly_page_raw_model_mismatch')
        rows.extend((page,row) for row in page.rows)
    counts=Counter(r.trade_date for _,r in rows if r.trade_date)
    proof_counts=Counter((p.page_fingerprint,p.row_fingerprint) for p in evidence)
    proofs={(p.page_fingerprint,p.row_fingerprint):p for p in evidence}
    current_start=date.fromisoformat(reference['start']) if reference and not reason else None
    for page,row in rows:
        day=row.trade_date.isoformat() if row.trade_date else None
        problems=[i for i in row.issues if 'trde_prica' not in i]
        problems.extend(i for i in page.issues if i!='daily_order_unverified')
        if row.volume==0:problems.append('zero_volume_week')
        if counts[row.trade_date]>1:problems.append('duplicate_weekly_date')
        key=(page.fingerprint,row.fingerprint);proof=proofs.get(key)
        if not proof or proof_counts[key]!=1:problems.append('bound_weekly_evidence_missing_or_duplicate')
        if proof:
            if proof.status=='incomplete':problems.append('incomplete_week')
            try:
                pstart,pend=date.fromisoformat(proof.period_start),date.fromisoformat(proof.period_end)
                if (proof.status!='final' or proof.price_unit!='KRW' or not proof.source.strip()
                    or proof.basis_id!=basis.get('id') or proof.session_basis!=basis.get('session_basis')
                    or not row.trade_date or not pstart<=row.trade_date<=pend
                    or not page.response_received_at<=timestamp(proof.verified_at)<=timestamp(proof.available_at)<=cutoff
                    or timestamp(proof.verified_at).date()<pend):problems.append('weekly_finality_or_basis_unverified')
            except (TypeError,ValueError,AttributeError):problems.append('weekly_finality_or_basis_unverified')
        if page.response_received_at>cutoff:problems.append('weekly_unavailable_at_cutoff')
        if current_start and row.trade_date and row.trade_date>=current_start:problems.append('reference_or_ongoing_week_excluded')
        if problems:held.append(dict(bar_date=day,reasons=sorted(set(problems))));continue
        valid[day]=(row,proof)
    if reason is None:
        for period in periods:
            item=valid.get(period['bar_date'])
            if (not item or item[1].period_start!=period['start'] or item[1].period_end!=period['end']
                or sum(1 for _,r in rows if r.trade_date and period['start']<=r.trade_date.isoformat()<=period['end'])!=1):
                reason='missing_or_invalid_completed_week';break
    with localcontext() as context:
        context.prec=50
        close=Decimal(daily['reference_close']['value']) if daily['reference_close'] else None
        high=max(valid[p['bar_date']][0].high for p in periods) if reason is None else None
        high52=dict(status='available' if high is not None else 'unavailable',reason=reason,
            definition='prior_52_completed_calendar_weeks_excluding_reference_week',required_weeks=52,
            prior_high=str(high) if high is not None else None,
            high_dates=[p['bar_date'] for p in periods if valid[p['bar_date']][0].high==high] if high is not None else [],
            distance_below_high_percent=str((high-close)/high*100) if high is not None and close is not None else None,
            rise_to_high_percent=str((high/close-1)*100) if high is not None and close is not None else None,
            close_breakout_confirmed=close>high if high is not None and close is not None else None,
            high_retest=close==high if high is not None and close is not None else None)
        if standalone:
            high52['close_comparison']=dict(status='unavailable',reason='daily_reference_close_unavailable')
    safe_periods=[{k:p.get(k) for k in ('start','end','bar_date')} for p in periods if isinstance(p,dict)]
    safe_reference={k:reference.get(k) for k in ('start','end')} if isinstance(reference,dict) else None
    result=dict(schema_version='arvis-hybrid-chart-v1',stock_code=daily['stock_code'],as_of=daily['as_of'],
        environment=daily['environment'],market=daily['market'],data_origin=daily['data_origin'],
        daily=daily,weekly=dict(api_id='ka10082',data_origin=weekly_data_origin,
            price_basis=deepcopy(basis) if known(basis,cutoff) else None,
            as_of=daily['as_of'],base_date=(pages[0].request.base_dt if pages else None),
            reference_week=safe_reference,selected_periods=safe_periods,high52=high52,
            quarantine=held),high52_source='weekly',primary_high52=deepcopy(high52),order_signal=False,
        notice='완료 주봉 52주의 고점은 일봉 252거래일 고점과 다른 지표. 일봉 계산·후보·주문 계약은 유지.')
    result['hybrid_reference']=fingerprint(result)
    return result
