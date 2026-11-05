"""Provisional daily arithmetic over parsed API bars; strict engine untouched."""
from decimal import Decimal,localcontext
from .chart import mean_close,volume_areas,compare,WINDOWS
from ..daily_chart import parse_response,fingerprint
from ..leader_tracking import timestamp


def analyze_observed_daily(pages,*,as_of,data_origin):
    pages=tuple(pages);cutoff=timestamp(as_of)
    if not pages or data_origin not in ('api_response','synthetic_fixture'):
        raise ValueError('daily_pages_and_origin_required')
    request=pages[0].request
    if request.api_id!='ka10081':raise ValueError('daily_api_required')
    rows=[]
    for page in pages:
        parsed=parse_response(page.raw,request=page.request,response_environment=page.request.environment,
            request_started_at=page.request_started_at,response_received_at=page.response_received_at,
            source=page.source,cont_yn=page.cont_yn,next_key=page.next_key,response_api_id=page.response_api_id)
        if parsed!=page or page.request!=request or page.response_received_at>cutoff:
            raise ValueError('daily_scope_or_cutoff_mismatch')
        if any(i!='daily_order_unverified' for i in page.issues):raise ValueError('daily_page_quality')
        rows.extend((page,r) for r in page.rows)
    if len({r.trade_date for _,r in rows})!=len(rows):raise ValueError('duplicate_daily_date')
    bars=[];held=[]
    for page,row in rows:
        if (row.trade_date is None or row.trade_date>=cutoff.date()
            or row.trade_date.strftime('%Y%m%d')>request.base_dt
            or any('trde_prica' not in i for i in row.issues) or row.volume==0):
            held.append(row.trade_date.isoformat() if row.trade_date else None);continue
        bars.append(dict(trade_date=row.trade_date.isoformat(),open=row.open,high=row.high,low=row.low,
            close=row.close,volume=row.volume,page_fingerprint=page.fingerprint,row_fingerprint=row.fingerprint))
    bars.sort(key=lambda b:b['trade_date'])
    return analyze_observed_bars(bars,stock_code=request.stock_code,environment=request.environment,
        market=request.market,as_of=as_of,data_origin=data_origin,base_date=request.base_dt,
        adjustment_request=request.adjustment,held_dates=held,
        page_fingerprints=[p.fingerprint for p in pages],units_basis='ka10081_documented_fields')


def analyze_observed_bars(bars,*,stock_code,environment,market,as_of,data_origin,
                          base_date,adjustment_request,held_dates=(),page_fingerprints=(),units_basis):
    """Shared provisional arithmetic after provider-specific validation, not an API parser."""
    cutoff=timestamp(as_of);bars=list(bars);held=list(held_dates)
    if any(b['trade_date']>=cutoff.date().isoformat() for b in bars):
        raise ValueError('observed_future_or_incomplete_day')
    if [b['trade_date'] for b in bars]!=sorted({b['trade_date'] for b in bars}):
        raise ValueError('sorted_unique_observed_bars_required')
    result=dict(schema_version='arvis-observed-daily-v1',status='provisional' if bars else 'unavailable',
        stock_code=stock_code,environment=environment,market=market,data_origin=data_origin,
        as_of=cutoff.isoformat(),base_date=base_date,adjustment_request=adjustment_request,
        price_basis_verified=False,calendar_verified=False,price_unit='KRW',volume_unit='share',
        units_basis=units_basis,bars_used=len(bars),held_dates=held,
        period=dict(start=bars[0]['trade_date'],end=bars[-1]['trade_date']) if bars else None,
        moving_averages={},trend={},supply_proxy=dict(status='unavailable',reason='insufficient_20_bars'),
        reference_close=None,unverified_reasons=['adjustment_and_corporate_actions_unverified',
            'observed_historical_dates_not_independent_trading_calendar','volume_adjustment_unverified'],
        order_signal=False,confirmed_buy_signal=False)
    with localcontext() as arithmetic:
        arithmetic.prec=50
        close=bars[-1]['close'] if bars else None
        if bars:result['reference_close']=dict(value=str(close),trade_date=bars[-1]['trade_date'],unit='KRW',
            kind='latest_saved_daily_close_not_live_quote',page_fingerprint=bars[-1]['page_fingerprint'],
            row_fingerprint=bars[-1]['row_fingerprint'])
        for n in WINDOWS:
            # A held bar in a requested recent window is not silently bridged.
            tail=bars[-n:];blocked=bool(tail and any(d is None or d>=tail[0]['trade_date'] for d in held))
            value=mean_close(bars,n) if not blocked else None
            previous=mean_close(bars[:-1],n) if not blocked else None
            result['moving_averages'][f'MA{n}']=dict(status='provisional' if value is not None else 'unavailable',
                value=str(value) if value is not None else None,position=compare(close,value),
                gap_percent=str((close/value-1)*100) if value is not None else None,
                reason=None if value is not None else 'insufficient_or_held_daily_bars',
                trade_dates=[b['trade_date'] for b in tail],
                slope=dict(status='provisional' if previous is not None and value is not None else 'unavailable',
                    definition='change_from_previous_observed_session_MA',
                    delta=str(value-previous) if previous is not None and value is not None else None,
                    percent=str((value/previous-1)*100) if previous is not None and value is not None else None))
        ma=result['moving_averages'];a=ma['MA20']['value'];b=ma['MA60']['value']
        result['trend']=dict(status='provisional' if a and b else 'unavailable',
            ma20_above_ma60=Decimal(a)>Decimal(b) if a and b else None,
            ma20_slope=ma['MA20']['slope'],ma60_slope=ma['MA60']['slope'])
        if len(bars)>=20 and ma['MA20']['status']=='provisional':
            areas=volume_areas(bars[-20:],close)
            result['supply_proxy']=dict(status='provisional',lookback_bars=20,
                method='top_five_positive_volume_bars_OHLC_proxy',areas=areas,
                overhead_resistance_possible=any(a['typical_above_observed_price'] for a in areas),
                notice='OHLCV estimate, not remaining investor holdings or order book liquidity')
    result['chart_bars']=[{k:str(v) for k,v in bar.items()} for bar in bars[-119:]]
    result['page_fingerprints']=list(page_fingerprints)
    result['analysis_reference']=fingerprint(result)
    return result
