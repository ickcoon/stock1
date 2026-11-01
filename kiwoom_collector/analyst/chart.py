"""Point-in-time completed-bar arithmetic and explicitly provisional risk guides."""
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext

from ..daily_chart import CODE, fingerprint
from ..leader_tracking import timestamp

WINDOWS = (3, 5, 20, 60, 200)


def number(value, *, volume=False):
    if type(value) not in (str, int, Decimal):
        return None
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0 or (not volume and result == 0):
        return None
    if volume and (result != result.to_integral_value() or result >= 4294967295):
        return None
    return result


def known(item, cutoff):
    try:
        return isinstance(item.get('source'),str) and bool(item['source'].strip()) and timestamp(item['available_at']) <= cutoff
    except (KeyError, ValueError, TypeError):
        return False


def compare(value, average):
    return None if value is None or average is None else 'above' if value > average else 'below' if value < average else 'equal'


def mean_close(observed_bars,count):
    """Shared arithmetic; callers retain their own validation/status policy."""
    if len(observed_bars)<count:return None
    return sum((b['close'] for b in observed_bars[-count:]),Decimal(0))/count


def volume_areas(observed_bars,price):
    """Existing top-five-volume OHLC proxy, no order or holding inference."""
    ranked=sorted((b for b in observed_bars if b['volume']>0),
        key=lambda b:(-b['volume'],b['trade_date']))[:5]
    supply=[]
    for bar in ranked:
        typical=(bar['high']+bar['low']+bar['close'])/3
        supply.append(dict(trade_date=bar['trade_date'],low=str(bar['low']),high=str(bar['high']),
            typical_price=str(typical),volume=str(bar['volume']),close=str(bar['close']),
            typical_above_observed_price=bool(price is not None and typical>price),
            distance_percent=str((typical/price-1)*100) if price is not None else None))
    return supply


def analyze(document):
    """Consume analyst-input-v1 JSON; external review assertions are not invented."""
    if document.get('schema_version') != 'analyst-input-v1':
        raise ValueError('explicit_analyst_input_required')
    code = document.get('stock_code')
    if not isinstance(code, str) or CODE.fullmatch(code) is None:
        raise ValueError('exact_stock_code_required')
    cutoff = timestamp(document['as_of'])
    if not isinstance(document.get('stock_name'), str) or not document['stock_name'].strip():
        raise ValueError('stock_name_required')
    environment = document.get('environment')
    market = document.get('market')
    expected_market = 'NXT' if code.endswith('_NX') else 'SOR' if code.endswith('_AL') else 'KRX'
    if environment not in ('mock', 'real') or market != expected_market or environment == 'mock' and market != 'KRX':
        raise ValueError('explicit_matching_market_scope_required')
    if document.get('data_origin') not in ('synthetic_fixture', 'api_response', 'unverified'):
        raise ValueError('explicit_data_origin_required')
    if any(document.get(k) is not None and not isinstance(document[k],dict)
           for k in ('price_basis','calendar','price','selection')):
        raise ValueError('analyst_metadata_objects_required')
    risks = []
    def risk(risk_code, **details):
        if risk_code not in {r['code'] for r in risks}:
            risks.append(dict(code=risk_code, **details))

    basis = document.get('price_basis') or {}
    basis_ok = (known(basis, cutoff) and basis.get('verified') is True
                and basis.get('corporate_actions_checked') is True
                and basis.get('comparable') is True
                and basis.get('mode') in ('adjusted', 'original') and bool(basis.get('id')))
    try:
        basis_ok = basis_ok and timestamp(basis['effective_at']) <= timestamp(basis['available_at']) <= cutoff
    except (KeyError, ValueError, TypeError):basis_ok = False
    if not basis_ok:risk('price_adjustment_or_corporate_actions_unverified')
    calendar = document.get('calendar') or {}
    calendar_ok = known(calendar, cutoff) and calendar.get('verified') is True
    try:calendar_ok = calendar_ok and timestamp(calendar['as_of']) == cutoff
    except (KeyError, TypeError, ValueError):calendar_ok = False
    sessions = []
    try:
        sessions = [date.fromisoformat(d) for d in calendar['completed_sessions']]
        if not sessions or len(set(sessions)) != len(sessions) or any(d > cutoff.date() for d in sessions):
            calendar_ok = False
        sessions.sort()
    except (KeyError, TypeError, ValueError):
        calendar_ok = False
    if not calendar_ok:
        risk('completed_trading_calendar_unverified')
        sessions = []
    expected = set(sessions)
    raw_bars = document.get('bars', [])
    if not isinstance(raw_bars, list):raise ValueError('bar_list_required')
    duplicates = {d for d, n in Counter(str(b.get('trade_date')) for b in raw_bars if isinstance(b, dict)).items() if n > 1}
    bars, quarantine = {}, []
    for index, row in enumerate(raw_bars):
        reasons = []
        if not isinstance(row, dict):
            quarantine.append(dict(input_index=index, reasons=['bar_object_required']))
            continue
        day_text = row.get('trade_date')
        try:day = date.fromisoformat(day_text)
        except (ValueError, TypeError):day = None;reasons.append('invalid_trade_date')
        if isinstance(day_text,str) and day_text in duplicates:reasons.append('duplicate_trade_date')
        if day and day > cutoff.date():reasons.append('future_trade_date')
        if day not in expected:reasons.append('not_confirmed_completed_session')
        if row.get('status') != 'final':reasons.append('bar_not_final')
        if not known(row, cutoff):reasons.append('bar_unavailable_at_cutoff')
        if any(row.get(k, document[k]) != document[k] for k in ('stock_code','environment','market')):
            reasons.append('bar_stock_or_environment_mismatch')
        try:
            observed, final, available = (timestamp(row[k]) for k in ('market_at', 'finalized_at', 'available_at'))
            if observed.date() != day or not observed <= final <= available <= cutoff:
                reasons.append('bar_finality_chronology_invalid')
        except (KeyError, TypeError, ValueError):reasons.append('bar_finality_chronology_invalid')
        if (row.get('basis_id') != basis.get('id') or row.get('price_unit') != 'KRW'
                or row.get('session_basis') != document.get('session_basis') or not document.get('session_basis')):
            reasons.append('bar_price_or_session_basis_mismatch')
        reasons.extend(row.get('price_issues', []))
        prices = {k:number(row.get(k)) for k in ('open', 'high', 'low', 'close')}
        if any(v is None for v in prices.values()):reasons.append('invalid_or_missing_ohlc')
        elif not prices['low'] <= min(prices['open'], prices['close']) <= max(prices['open'], prices['close']) <= prices['high']:
            reasons.append('inconsistent_ohlc')
        if reasons:
            # No future price/volume or future selection payload is exposed.
            quarantine.append(dict(input_index=index, trade_date=day_text if isinstance(day_text,str) else None,
                                   reasons=list(dict.fromkeys(reasons))))
            continue
        volume = number(row.get('volume'), volume=True) if row.get('volume_unit') == 'share' else None
        volume_issues = list(row.get('volume_issues', []))
        if volume is None:volume_issues.append('invalid_or_unverified_volume')
        elif volume == 0:volume_issues.append('zero_volume_no_supply_inference')
        if volume_issues:
            risk('volume_quality_incomplete')
        bars[day] = dict(prices, volume=volume, volume_issues=volume_issues,
            trade_date=day.isoformat(), source=row['source'], available_at=timestamp(row['available_at']).isoformat(),
            market_at=timestamp(row['market_at']).isoformat(), finalized_at=timestamp(row['finalized_at']).isoformat(),
            source_reference=deepcopy(row.get('source_reference')), original_index=index,
            api_raw_values={k:v for k,v in (row.get('api_raw_values') or {}).items() if k in (
                'dt','open_pric','high_pric','low_pric','cur_prc','trde_qty','upd_rt','upd_stkpc_tp')},
            source_values={k:str(row[k]) if row.get(k) is not None else None for k in ('open','high','low','close','volume')})
    if quarantine:risk('bars_quarantined', count=len(quarantine))
    if sessions:
        missing = [d.isoformat() for d in sessions[-200:] if d not in bars]
        if missing:risk('missing_or_invalid_completed_bars', trade_dates=missing)
    observed_price = document.get('price') or {}
    price = number(observed_price.get('value'))
    price_ok = known(observed_price, cutoff) and price is not None
    try:
        if not timestamp(observed_price['observed_at']) <= timestamp(observed_price['available_at']) <= cutoff:
            price_ok = False
    except (KeyError, TypeError, ValueError):price_ok = False
    if observed_price.get('basis_id') != basis.get('id') or observed_price.get('unit') != 'KRW':price_ok = False
    if any(observed_price.get(k, document[k]) != document[k] for k in ('stock_code','environment','market')):price_ok = False
    if not price_ok:
        risk('observed_price_unavailable_or_incompatible')
        price = None
    selection = document.get('selection') or {}
    selection_ok = known(selection, cutoff)
    if selection.get('stock_code', code) != code:selection_ok = False
    if not selection_ok:risk('selection_evidence_unavailable_at_cutoff')
    if document['data_origin'] == 'unverified':risk('data_origin_unverified')
    averages, values, disparities = {}, {}, {}
    with localcontext() as context:
        context.prec = 50
        for count in WINDOWS:
            window = sessions[-count:]
            reason = ('price_basis_unverified' if not basis_ok else 'calendar_unverified' if not calendar_ok
                      else 'insufficient_completed_sessions' if len(window) < count
                      else 'missing_or_invalid_bar_in_window' if any(d not in bars for d in window) else None)
            value = mean_close([bars[d] for d in window],count) if reason is None else None
            values[count] = value
            averages['MA'+str(count)] = dict(value=str(value) if value is not None else None,
                status='available' if value is not None else 'unavailable', reason=reason, required_bars=count,
                usable_bars=sum(d in bars for d in window), trade_dates=[d.isoformat() for d in window],
                closes=[str(bars[d]['close']) if d in bars else None for d in window])
            if reason:risk('ma'+str(count)+'_unavailable', reason=reason)
        for count in (3, 5):
            ma = values[count]
            disparities['MA'+str(count)] = dict(
                price_to_ma_percent=str(price/ma*100) if price is not None and ma is not None else None,
                gap_percent=str((price/ma-1)*100) if price is not None and ma is not None else None)
        comparisons = {'MA'+str(n):compare(price, values[n]) for n in (5,20,200)}
        alignment = compare(values[20], values[60])
        if alignment == 'below':risk('ma20_below_ma60')
        if comparisons['MA200'] == 'below':risk('price_below_ma200')
        if price is None or values[5] is None or values[20] is None:
            zone, guide = 'unavailable', 'data_review_required'
        elif price < values[20]:
            zone, guide = 'below_ma20', 'avoidance_priority'
            risk('price_below_ma20')
        elif price >= values[5]:
            zone, guide = 'above_or_at_ma5', 'aggressive_review_possible'
        else:
            zone, guide = 'below_ma5_at_or_above_ma20', 'conservative_review'
            risk('price_below_ma5')
        profile_dates = sessions[-20:]
        if basis.get('volume_comparable') is not True:risk('volume_adjustment_basis_unverified')
        profile_ok = (basis_ok and basis.get('volume_comparable') is True and len(profile_dates) == 20 and all(d in bars and bars[d]['volume'] is not None
                      and not bars[d]['volume_issues'] for d in profile_dates))
        supply = []
        if profile_ok:
            supply=volume_areas([bars[d] for d in profile_dates],price)
        if not profile_ok:risk('twenty_bar_volume_proxy_unavailable')
        overhead = any(s['typical_above_observed_price'] for s in supply) if price is not None and profile_ok else None
        if overhead:risk('estimated_overhead_supply')
    if not basis_ok or not calendar_ok or not selection_ok or document['data_origin'] == 'unverified':
        status = 'data_review_required'
    else:status = guide
    result = dict(schema_version='arvis-analyst-v1', stock_code=code, stock_name=document['stock_name'],
        as_of=cutoff.isoformat(), environment=environment, market=market, data_origin=document['data_origin'],
        selection=deepcopy(selection) if selection_ok else None,
        price=deepcopy(observed_price) if price_ok else None,
        price_age_seconds=str((cutoff-timestamp(observed_price['observed_at'])).total_seconds()) if price_ok else None,
        moving_averages=averages, price_comparisons=comparisons, trend_state=alignment or 'unavailable',
        trend_basis='MA20 compared with MA60; not a slope or forecast', short_disparity=disparities,
        overheat_assessment='not_rated_no_threshold', price_zone=zone, zone_guide=guide, review_status=status,
        supply_proxy=dict(status='available' if profile_ok else 'unavailable', lookback_bars=20,
            method='top five positive-volume completed bars; OHLC typical price and low-high ranges',
            areas=supply, overhead_resistance_possible=overhead,
            notice='일봉 OHLCV 기반 추정치. 실제 가격대별 거래량·보유자 원가·확정 저항이 아닙니다.'),
        risks=risks, evidence=dict(price_basis=deepcopy(basis) if known(basis,cutoff) else None,
            calendar=deepcopy(calendar) if calendar_ok else None,
            calendar_source=calendar.get('source') if calendar_ok else None,
            last_completed_session=sessions[-1].isoformat() if sessions else None,
            bars=[dict(b, **{k:str(b[k]) if b[k] is not None else None for k in ('open','high','low','close','volume')})
                  for _,b in sorted(bars.items())], quarantine=quarantine),
        extensions=dict(news_analysis=dict(status='not_analyzed', report_reference=None,
                                           input_contract='arvis-news-analysis-v1')),
        order_signal=False, position_size=None,
        notice='매매 판단 보조용 잠정 검토 가이드이며 매수·매도 신호나 고정 비중이 아닙니다.')
    result['analysis_reference'] = fingerprint(result)
    return result
