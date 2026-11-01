"""Offline dashboard projection of existing C14 results; never select or trade."""
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal,localcontext
from .chart import compare
from ..leader_tracking import timestamp
from ..daily_chart import fingerprint


def chart_card(report,*,stock_name=None,observed_daily=None,mini_chart_output=None):
    schema=report.get('schema_version')
    if schema not in ('arvis-hybrid-chart-v1','arvis-chart-context-v1'):
        raise ValueError('chart_card_input_schema_invalid')
    hybrid=schema=='arvis-hybrid-chart-v1'
    daily=report['daily'] if hybrid else report
    for key in ('stock_code','environment','market','data_origin','as_of'):
        if daily.get(key)!=report.get(key):raise ValueError('chart_card_scope_mismatch')
    cutoff=timestamp(report['as_of'])
    primary=report['primary_high52'] if hybrid else daily['high52']
    provisional=report.get('provisional_high52',{})
    verified=primary.get('status')=='available'
    status='verified' if verified else 'provisional' if provisional.get('status')=='provisional' else 'unavailable'
    selected=primary if verified else provisional
    high=dict(status=status,value=selected.get('prior_high') if status!='unavailable' else None,
        unit='KRW',primary=deepcopy(primary),provisional=deepcopy(provisional) or None,
        distance_status=provisional.get('distance_status','unavailable'),
        price_vs_high_percent=provisional.get('price_vs_high_percent'),
        distance_band=deepcopy(provisional.get('distance_band')),
        comparison_price=deepcopy(provisional.get('comparison_price')),
        age_weeks=dict(status='unavailable',value=None,reason='high_week_unavailable'))
    # Ties use the most recent occurrence. Whole calendar weeks, not trading weeks.
    dates=selected.get('high_dates',[]) if verified else [w['start'] for w in provisional.get('high_weeks',[])]
    if status!='unavailable' and dates:
        day=max(date.fromisoformat(d) for d in dates)
        current=cutoff.date()-timedelta(days=cutoff.date().weekday())
        week=day-timedelta(days=day.weekday())
        if week<=current:high['age_weeks']=dict(status=status,value=(current-week).days//7,
            occurrence_week_start=week.isoformat(),as_of_week_start=current.isoformat(),tie_policy='latest_occurrence')
    if not hybrid:
        high.update(distance_status='verified' if verified else 'unavailable',
            price_vs_high_percent=primary.get('close_vs_high_percent'),
            comparison_price=deepcopy(daily.get('reference_close')))
        if verified and high['price_vs_high_percent'] is not None:
            from .hybrid import high52_distance_band
            high['distance_band']=high52_distance_band(Decimal(high['price_vs_high_percent']))
    close=daily.get('reference_close');averages={}
    for n in (3,5,20,60,200):
        key=f'MA{n}';source=daily.get('moving_averages',{}).get(key,{})
        usable=source.get('status')=='available' and close is not None
        value=Decimal(source['value']) if usable else None
        price=Decimal(close['value']) if usable else None
        with localcontext() as arithmetic:
            arithmetic.prec=50
            averages[key]=dict(status='verified' if usable else 'unavailable',
                value=source.get('value'),position=compare(price,value) if usable else None,
                gap_percent=str((price/value-1)*100) if usable else None,
                reason=None if usable else source.get('reason') or 'daily_reference_close_unavailable',
                evidence=deepcopy(source))
    supply=deepcopy(daily.get('supply_proxy',dict(status='unavailable',reason='daily_input_missing')))
    resistance=dict(status='verified' if supply.get('status')=='available' else 'unavailable',
        lookback_bars=20,caution=supply.get('overhead_resistance_possible'),analysis=supply)
    result=dict(schema_version='arvis-chart-card-v1',stock_code=report['stock_code'],
        stock_name=stock_name or daily.get('stock_name'),as_of=report['as_of'],environment=report['environment'],
        market=report['market'],data_origin=report['data_origin'],high52=high,moving_averages=averages,
        resistance=resistance,explanation=deepcopy(daily.get('explanation')),
        provenance=dict(source_schema=schema,source_reference=report.get('hybrid_reference') or report.get('context_reference'),
            input_basis=deepcopy(report.get('input_basis')),daily_reference_close=deepcopy(close),
            captured_values_are_not_live_quotes=True),
        candidate_selection_changed=False,order_signal=False,confirmed_buy_signal=False)
    if observed_daily is not None:
        observed=observed_daily
        if (observed.get('schema_version')!='arvis-observed-daily-v1'
            or any(observed.get(k)!=report.get(k) for k in ('stock_code','environment','market','data_origin','as_of'))):
            raise ValueError('card_observed_daily_scope_mismatch')
        for key,value in observed['moving_averages'].items():
            if result['moving_averages'][key]['status']=='unavailable':
                result['moving_averages'][key]=deepcopy(value)
        if result['resistance']['status']=='unavailable':
            supply=deepcopy(observed['supply_proxy'])
            result['resistance']=dict(status=supply['status'],lookback_bars=20,
                caution=supply.get('overhead_resistance_possible'),analysis=supply)
        result['daily_trend']=deepcopy(observed['trend'])
        result['provenance']['observed_daily']=deepcopy(observed)
        quote=observed.get('reference_close')
        if (high['status']=='provisional' and quote and provisional.get('adjustment_request')==observed.get('adjustment_request')):
            from .hybrid import high52_distance_band
            with localcontext() as arithmetic:
                arithmetic.prec=50
                distance=(Decimal(quote['value'])/Decimal(high['value'])-1)*100
            high.update(distance_status='provisional',price_vs_high_percent=str(distance),
                distance_band=high52_distance_band(distance),comparison_price=deepcopy(quote),
                comparison_basis='matching_request_option_effective_adjustment_unverified')
    result['card_reference']=fingerprint(result)
    if mini_chart_output is not None:
        from .mini_chart import with_mini_chart
        result=with_mini_chart(result,observed_daily or {},mini_chart_output)
    return result
