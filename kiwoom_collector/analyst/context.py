"""Offline completed-close context; reuse C14-A validation, never fetch or rank."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

from .chart import analyze, compare, known, WINDOWS
from ..daily_chart import fingerprint
from ..leader_tracking import timestamp


def analyze_context(document, *, candidate_context=None):
    """Return arvis-chart-context-v1; 252-session reference excludes the latest bar."""
    analysis = analyze(document)
    cutoff = timestamp(analysis['as_of'])
    candidate = None
    if candidate_context is not None:
        if (not isinstance(candidate_context, dict)
                or any(candidate_context.get(k) != analysis[k]
                       for k in ('stock_code', 'environment', 'market', 'data_origin'))
                or not known(candidate_context, cutoff)
                or not isinstance(candidate_context.get('decision'), dict)
                or candidate_context['decision'].get('state') not in (
                    'analysis_candidate', 'confirmed_excluded', 'provisional_excluded', 'pending', 'unmatched')):
            raise ValueError('candidate_context_scope_or_evidence_invalid')
        candidate = deepcopy(candidate_context)

    calendar = analysis['evidence']['calendar']
    sessions = sorted(date.fromisoformat(day).isoformat() for day in calendar['completed_sessions']) if calendar else []
    day = sessions[-1] if sessions else None
    bars = {b['trade_date']: b for b in analysis['evidence']['bars']}
    issues = {r['code'] for r in analysis['risks']}
    basis_ok = 'price_adjustment_or_corporate_actions_unverified' not in issues
    current = bars.get(day) if basis_ok else None
    close = Decimal(current['close']) if current else None
    averages = {f'MA{n}': analysis['moving_averages'][f'MA{n}']['value'] for n in WINDOWS}
    values = {key: Decimal(value) if value is not None else None for key, value in averages.items()}
    missing = [key for key, value in values.items() if value is None]
    if close is None: missing.append('reference_close')

    with localcontext() as precision:
        precision.prec = 50
        comparisons = {key: compare(close, value) for key, value in values.items()}
        disparity = {key: dict(
            price_to_ma_percent=str(close/value*100) if close is not None and value is not None else None,
            gap_percent=str((close/value-1)*100) if close is not None and value is not None else None)
            for key, value in values.items() if key in ('MA3', 'MA5')}
        alignment = compare(values['MA20'], values['MA60'])
        factors = dict(ma20_above_ma60=None if alignment is None else alignment == 'above',
            close_above_ma200=None if comparisons['MA200'] is None else comparisons['MA200'] == 'above')

        prior = sessions[:-1][-252:]
        missing_days = [d for d in prior if d not in bars]
        reason = ('price_basis_unverified' if not basis_ok else 'calendar_unverified' if not calendar
                  else 'insufficient_prior_sessions' if len(prior) < 252
                  else 'missing_or_invalid_prior_bar' if missing_days
                  else 'reference_close_unavailable' if close is None else None)
        high = max(Decimal(bars[d]['high']) for d in prior) if reason is None else None
        high52 = dict(status='available' if high is not None else 'unavailable', reason=reason,
            definition='prior_252_completed_sessions_excluding_reference', required_prior_sessions=252,
            usable_prior_bars=sum(d in bars for d in prior), reference_trade_date=day,
            window_start=prior[0] if prior else None, window_end=prior[-1] if prior else None,
            missing_trade_dates=missing_days, prior_high=str(high) if high is not None else None,
            high_dates=[d for d in prior if Decimal(bars[d]['high']) == high] if high is not None else [],
            distance_below_high_percent=str((high-close)/high*100) if high is not None else None,
            rise_to_high_percent=str((high/close-1)*100) if high is not None else None,
            close_vs_high_percent=str((close/high-1)*100) if high is not None else None,
            close_breakout_confirmed=close > high if high is not None else None,
            high_retest=close == high if high is not None else None)
        factors['new_252_session_high'] = high52['close_breakout_confirmed']
        if high is None: missing.append('high52')

        supply = analysis['supply_proxy']
        areas = []
        if supply['status'] == 'available' and close is not None:
            for original in supply['areas']:
                area = deepcopy(original)
                typical = Decimal(area['typical_price'])
                area.update(typical_above_reference_close=typical > close,
                    distance_from_close_percent=str((typical/close-1)*100),
                    relative_to_prior_high=(None if high is None else
                        'below' if Decimal(area['high']) < high else
                        'above' if Decimal(area['low']) > high else 'overlapping'))
                # C14-A's observed-price comparison is not a completed-close fact.
                area.pop('typical_above_observed_price', None)
                area.pop('distance_percent', None)
                areas.append(area)
        supply_ok = supply['status'] == 'available' and close is not None
        above = [a for a in areas if a['typical_above_reference_close']]
        nearest = min(above, key=lambda a: Decimal(a['distance_from_close_percent'])) if above else None
        resistance = dict(status='available' if supply_ok else 'unavailable', lookback_bars=20,
            method=supply['method'], areas=areas,
            overhead_resistance_possible=bool(above) if supply_ok else None,
            nearest_overhead_area=deepcopy(nearest), notice=supply['notice'])
        if not supply_ok: missing.append('supply_proxy')
        distance_order = None
        if high is not None and high >= close and nearest:
            a = Decimal(high52['rise_to_high_percent'])
            b = Decimal(nearest['distance_from_close_percent'])
            distance_order = 'prior_high_nearer' if a < b else 'supply_nearer' if a > b else 'equal_distance'

        if (close is None or values['MA5'] is None or values['MA20'] is None
                or analysis['data_origin'] == 'unverified' or analysis['selection'] is None):
            state, label = 'data_review_required', '데이터 검토 필요'
        elif close < values['MA20']:
            state, label = 'avoidance_candidate', '매매 회피 후보'
        elif close < values['MA5']:
            state, label = 'conservative_observation', '매매 보수'
        elif above:
            state, label = 'resistance_caution', '저항 주의'
        elif factors['ma20_above_ma60'] and factors['close_above_ma200']:
            state, label = 'trend_good', '추세 양호'
        else:
            state, label = 'active_observation', '적극 관찰 가능'
        explanations = [f'완료봉 종가 기준: {day}' if current else '검증된 기준 완료봉 없음']
        explanations.extend(f'종가 / {key}: {value}' for key, value in comparisons.items())
        if above: explanations.append('최근 20봉 OHLCV 추정 가격대가 종가 위에 존재')
        if missing: explanations.append('데이터 부족·검토 필요: '+', '.join(missing))
        if candidate and candidate['decision']['state'] != 'analysis_candidate':
            explanations.append('C12 제외·보류 판정을 유지하며 분석으로 후보 승격하지 않음')

    result = dict(schema_version='arvis-chart-context-v1', stock_code=analysis['stock_code'],
        stock_name=analysis['stock_name'], as_of=analysis['as_of'], environment=analysis['environment'],
        market=analysis['market'], data_origin=analysis['data_origin'], analysis=analysis,
        reference_close=(dict(trade_date=day, value=str(close), unit='KRW',
            basis_id=document['price_basis']['id'], market_at=current['market_at'],
            available_at=current['available_at'], source=current['source'],
            source_reference=deepcopy(current['source_reference'])) if current else None),
        moving_averages=deepcopy(analysis['moving_averages']), close_comparisons=comparisons,
        close_short_disparity=disparity, high52=high52, supply_proxy=resistance,
        high_vs_supply_distance=distance_order, supporting_factors=factors,
        candidate_context=candidate, candidate_eligibility_changed=False,
        coverage=dict(status='complete' if not missing else 'partial', unavailable=missing),
        explanation=dict(state=state, label=label, reasons=explanations),
        scoring=dict(applied=False, notice='가산 근거만 제공; 후보 순위·가중치 자동 변경 없음'),
        order_signal=False, position_size=None,
        notice='완료봉 기준 매매 판단 보조. OHLCV 매물대는 추정이며 실제 매도 잔량이 아닙니다.')
    result['context_reference'] = fingerprint(result)
    return result
