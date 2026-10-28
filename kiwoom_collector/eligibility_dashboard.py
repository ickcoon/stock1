"""Offline Step 6-D review dashboard over saved JSON. No DB/auth/network entrypoint."""
import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

from . import bubble_cross as bubble, capital_filter as capital, cross_adapter as adapter
from .market_cap import numeric
from .radar_v2 import e

BANDS = dict(below_floor='기본 하한 미달', primary_exploration='핵심 탐색',
             extended_exploration='확장 탐색', mega_cap_leader='초대형 주도주', unknown='미확인')
REASONS = dict(market_cap_missing='시총 관측 없음', market_cap_future='선택 시점 이후 시총 차단',
    market_cap_code_mismatch='시총 종목코드 불일치', market_cap_date_mismatch='시총 관측 날짜 불일치',
    duplicate_capital_records='중복 시총 격리', turnover_not_available='이 회차의 거래대금 자료 없음',
    turnover_future='선택 시점 이후 거래대금 차단', turnover_date_mismatch='거래대금 날짜 불일치',
    market_unverified='시장 조건 미확인', market_mismatch='시장 조건 불일치',
    source_incomplete='원본 회차·페이지·행 검증 미완료', security_type_separate='ETF·ETN 등 별도 유형 심사',
    security_type_unverified='일반 주식 유형 미확인', security_type_mismatch='종목 유형 근거 불일치',
    market_cap_evidence_unverified='시총 단위·기준 근거 미확인',
    closing_turnover_evidence_unverified='장마감 누적 금액 근거 미확인',
    market_time_or_trade_date_unverified='시장 기준시각·거래일 미확인',
    capital_market_mismatch='시총 거래소와 시장 조건 불일치',
    turnover_market_time_after_response='거래대금 시장 기준시각이 응답 시각 이후',
    market_cap_observation_unknown='시총 관측 구간 미확인',
    cross_context_evidence_missing='교차 자료에 결속된 검증 근거 없음',
    sector_co_rise_not_confirmed='주도섹터 동반 상승 미확인')


def observed(source, cutoff):
    """Never substitute retrieval dates for market timestamps."""
    start = adapter.timestamp(source.get('request_started_at'))
    end = adapter.timestamp(source.get('response_received_at'))
    if not start or not end or end < start:
        return 'unknown'
    if start.date() != cutoff.date() or end.date() != cutoff.date():
        return 'date_mismatch'
    return 'future' if end > cutoff else 'available'


def review(report, records, *, at, proofs=None):
    """Use the existing evidence engine, with strict dashboard chronology gates."""
    cutoff = adapter.timestamp(at)
    if cutoff is None:
        raise ValueError('Timezone-aware review time required')
    env = report['sources']['ranking']['environment']
    if any(c.get('environment') != env for c in records):
        raise ValueError('Mixed capital environment forbidden')
    counts = Counter(c.get('requested_code') for c in records)
    visible, blocked = [], {}
    for cap in records:
        code = cap.get('requested_code')
        reason = ('duplicate_capital_records' if counts[code] > 1 else
                  'market_cap_code_mismatch' if cap.get('raw', {}).get('stk_cd') != code else None)
        state = observed(cap, cutoff)
        if not reason and state != 'available':
            reason = 'market_cap_' + (state if state != 'unknown' else 'observation_unknown')
        if reason:
            blocked.setdefault(code, []).append(reason)
        else:
            visible.append(cap)
    result = capital.integrate(report, visible, reference_at=at, proofs=proofs)
    cash_state = observed(report['sources']['turnover'], cutoff)
    markets = report.get('market_match')
    a, b = (report['sources'][key] for key in ('ranking', 'turnover'))
    source_ok = (a.get('status') == b.get('status') == 'success' and bool(b.get('pagination_complete'))
                 and not any(report.get('review', {}).get(key) for key in ('ranking', 'turnover')))
    cap_by_code = {c['requested_code']: c for c in visible}
    for row in result['rows']:
        reasons = list(blocked.get(row['stock_code'], []))
        if cash_state != 'available':
            reasons.append('turnover_' + (cash_state if cash_state != 'unknown' else 'not_available'))
        if markets != 'declared_match_unverified':
            reasons.append('market_mismatch' if markets == 'mismatch' else 'market_unverified')
        if not source_ok:
            reasons.append('source_incomplete')
        cap = cap_by_code.get(row['stock_code'])
        kind = row.get('security_type', 'unknown')
        if kind != 'common_stock':
            reasons.append('security_type_unverified' if kind == 'unknown' else 'security_type_separate')
        if cap and cap.get('security_type') != kind:
            reasons.append('security_type_mismatch')
        if cap and (cap.get('venue') != 'KRX' or (b.get('market') or {}).get('stex_tp') != '1'):
            reasons.append('capital_market_mismatch')
        cash_visible = (cash_state == 'available' and markets != 'mismatch'
                        and bool(report['timing']['collection_window_provisional']))
        row['turnover_visible'] = cash_visible
        # Only source metadata needed by the panel; no full rows in derived UI state.
        row['source_times'] = {k: {f: s.get(f) for f in ('id', 'api_id', 'environment', 'source_kind',
            'test_mode', 'collected_at', 'request_started_at', 'response_received_at', 'market')}
            for k, s in report['sources'].items()}
        if cap and row['capital_reference']:
            row['capital_reference'].update(api_id=cap.get('api_id'), environment=cap['environment'],
                request_started_at=cap.get('request_started_at'), response_received_at=cap.get('response_received_at'))
        q = row['qualification']
        # Closing turnover evidence is independent of whether a cap was collected.
        witness = (proofs or {}).get(row['stock_code'], {})
        cash_proof = witness.get('turnover', {})
        cash_asof = adapter.timestamp(cash_proof.get('market_asof'))
        cash_received = adapter.timestamp(b.get('response_received_at'))
        if cash_asof and cash_received and cash_asof > cash_received:
            reasons.append('turnover_market_time_after_response')
        cash_value = numeric(row.get('turnover_won_diagnostic'))
        cash_raw = numeric(row.get('turnover_raw'))
        cash_verified = (witness.get('cross_context') == capital.context_key(report, row)
            and cash_proof.get('basis') == 'closing_cumulative'
            and all(cash_proof.get(k) is True for k in ('unit_runtime_verified', 'closing_basis_verified', 'market_conditions_verified'))
            and cash_proof.get('evidence') and cash_asof == cutoff
            and cash_proof.get('trade_date') == cutoff.date().isoformat()
            and cash_value is not None and cash_raw is not None and cash_raw >= 0
            and cash_value == cash_raw * Decimal(1000000))
        if cash_verified:
            q['checks']['closing_turnover_1000_eok'] = cash_value >= Decimal(100000000000)
        row['turnover_phase'] = 'closing_verified' if cash_verified and not reasons else 'intraday_provisional'
        if reasons:
            q.update(final_review_allowed=False, sector_co_rise=None,
                     checks={k: None for k in q['checks']})
            row['market_cap_won_verified'] = None
            row['capital_band'] = capital.capital_band(None)
        q['reasons'] = list(dict.fromkeys(reasons + q['reasons']))
        separate = 'security_type_separate' in reasons
        q['review_status'] = ('검토 가능' if q['final_review_allowed'] else
            '기준 미달' if not reasons and any(v is False for v in q['checks'].values()) else '검증 보류')
        row['separate_type_review'] = separate
        row['candidate_confirmed'] = q['candidate_confirmed'] = False
    return result


def build(document, *, capital_records=(), capital_proofs=None):
    bundle = bubble.build(document)  # Geometry, colors and timeline rules stay unchanged.
    report = bundle['comparisons'][1000]
    bundle['eligibility'] = review(report, capital_records, at=document['ranking']['collected_at'],
                                   proofs=capital_proofs)
    return bundle


def amount_eok(won):
    value = numeric(won)
    return f'{value / Decimal(100000000):,.2f}억원' if value is not None and value >= 0 else '미확인'


def label(value):
    return '기준 충족' if value is True else '기준 미달' if value is False else '미확인'


def card(stock, row, *, membership='이전 관측 없음'):
    row = row or {}
    q = row.get('qualification', {})
    checks = q.get('checks', {})
    cap = row.get('capital_reference')
    cash = row.get('turnover_visible', False)
    raw = cap.get('raw', {}) if cap else {}
    band = row.get('capital_band', {})
    reference = row.get('capital_band_reference', {})
    band_text = BANDS.get(band.get('band'), '미확인')
    if band_text == '미확인' and cap:
        band_text += ' · 참고 구간 ' + BANDS.get(reference.get('band'), '미확인') + ' (미검증)'
    metrics = [('0198 관심순위', str(stock['rank'])+'위'),
        ('TOP5 반복 출현', str(row.get('top5_appearances', stock.get('top5_count', 0)))+'회'),
        ('0186 거래대금 순위', str(row['turnover_rank'])+'위' if cash and row.get('turnover_rank') else '미확인'),
        ('거래대금 · 억원 환산', amount_eok(row.get('turnover_won_diagnostic')) if cash else '미확인'),
        ('시총 · 억원 환산', amount_eok(cap.get('won_diagnostic')) if cap else '미확인'),
        ('종합 적격성', q.get('review_status', '검증 보류'))]
    details = [('TOP20 상태', membership), ('소속 주도섹터 · 관심도 기준', ' / '.join(row.get('sectors', [])) or '미분류'),
        ('일반 주식 심사', '별도 유형 심사' if row.get('separate_type_review') else row.get('security_type', '미확인')),
        ('거래대금 관측 구분', '장 마감 검증값' if row.get('turnover_phase') == 'closing_verified' and cash else '장중 잠정값 / 장마감 미검증'),
        ('0186 원문 거래대금 · 백만원', row.get('turnover_raw') if cash else '미확인'),
        ('당일 등락률 · 원문', str(row['change_percent_raw'])+'%' if cash and row.get('change_percent_raw') is not None else '미확인'),
        ('ka10001 원본 mac · 명세 억원', raw.get('mac', '미확인')), ('원본 현재가 / flo_stk', str(raw.get('cur_prc', '미확인'))+' / '+str(raw.get('flo_stk', '미확인'))),
        ('시총 3,000억원 이상', label(checks.get('market_cap_3000_eok'))),
        ('장 마감 누적 1,000억원 이상', label(checks.get('closing_turnover_1000_eok'))),
        ('거래대금 순위 40위 이내', label(checks.get('turnover_rank_40'))),
        ('시총 구간', band_text), ('주도섹터 동반 상승', '확인' if q.get('sector_co_rise') is True else '미확인')]
    reason = ' · '.join(REASONS.get(r, '검증 근거 확인 필요 ('+r+')') for r in q.get('reasons', [])) or ('검토 근거 확인 완료' if q.get('final_review_allowed') else '이 회차의 교차 자료 없음')
    provenance = row.get('source_times', {})
    sources = ''.join('<p>'+e(name)+' · '+e(json.dumps(value, ensure_ascii=False, sort_keys=True))+'</p>' for name, value in provenance.items())
    if cap:
        sources += '<p>ka10001 · '+e(json.dumps(cap, ensure_ascii=False, sort_keys=True))+'</p>'
    return ('<article class="stock-card eligibility-card" data-confirmed="false" data-code="'+e(stock['code'])+'">'
        '<header><b>'+e(stock['name'] or stock['code'])+'</b><code>'+e(stock['code'])+'</code></header>'
        '<div class="stock-metrics">'+''.join('<span>'+e(k)+'<b>'+e(v)+'</b></span>' for k,v in metrics)+'</div>'
        '<p class="stock-status">'+e(q.get('review_status', '검증 보류'))+' · 자동 매수 승인 아님 · candidate_confirmed=false</p>'
        '<details class="eligibility-details"><summary>세부 수치·검증 상태·보류 사유</summary><dl>'+''.join('<dt>'+e(k)+'</dt><dd>'+e(v)+'</dd>' for k,v in details)+'</dl>'
        '<p class="hold-reasons">'+e(reason)+'</p><details><summary>출처와 관측 시각</summary>'+sources+'</details>'
        '<p>가격×상장주식수 검산은 조건부 산술이며 API 단위 검증을 대신하지 않습니다. 5조·10조 초과 자동 제외 없음.</p></details></article>')


CSS = '''.eligibility-details{font-size:11px;line-height:1.6;overflow-wrap:anywhere}
.eligibility-details dl{display:grid;grid-template-columns:minmax(110px,1fr) minmax(0,1.5fr);gap:5px}
.eligibility-details dt{color:#596a7d}.eligibility-details dd{margin:0;font-weight:600}
.eligibility-details p{overflow-wrap:anywhere}.hold-reasons{padding:8px;background:#eef2f7;border-radius:6px}
.eligibility-top{display:flex;flex-wrap:wrap;gap:6px}.eligibility-top label{font-size:11px;padding:7px;border:1px solid #ccd8e3;border-radius:6px;cursor:pointer}
@media(min-width:851px){.eligibility-card>.eligibility-details{padding-top:5px}}
@media(max-width:540px){.eligibility-details dl{grid-template-columns:minmax(95px,1fr) minmax(0,1.2fr)}}'''


def frame_html(frame, bundle):
    html = bubble.frame_html(frame, bundle)
    active = frame['collection_id'] == bundle['source']['ranking']['id']
    rows = {r['stock_code']: r for r in bundle['eligibility']['rows']} if active else {}
    repeats = {s['code']: s['top5_appearances'] for s in frame['data']['repeated_stocks']}
    groups = [(g['id'],g['name'],g['members']) for g in frame['model']['groups']]
    current = frame['data']['current']
    groups.append(('unclassified','미분류',[dict(code=s['stk_cd'],name=s['stk_nm'],rank=s['rank'])
        for s in current['stocks'] if not s['tags']] if current else []))
    parts = ['<section class="cross-section" aria-label="주도주 매매 적격성 검토"><h2>주도주 매매 적격성 검토</h2>',
        '<p class="cross-note">TOP20 섹터 탐지 · TOP5 반복 우선 관찰 · 장중 미달도 관찰 유지 · 점수 합산 없음</p>']
    if bundle['source'].get('source_kind') == 'fixture':
        parts.append('<p class="cross-empty">합성 fixture 검토 화면 · 검증 플래그도 테스트용이며 실측 근거가 아닙니다.</p>')
    parts.append('<div class="eligibility-top">')
    for g in frame['focus']['top']:
        members = next((m for gid,_,m in groups if gid == g['id']), [])
        live = [s for s in members if s['rank'] is not None]
        core = [s['name'] or s['code'] for s in live if s['rank'] <= 5]
        parts.append('<label for="pick-'+e(g['id'])+'">'+e(g['name'])+' · TOP20 '+str(len(live))+'개 · TOP5 '+e(' / '.join(core) or '없음')+'</label>')
    parts.append('</div>')
    if not active:
        parts.append('<p class="cross-empty">이 회차에 결합된 금융 자료 없음 · 미래 관측을 과거에 표시하지 않습니다.</p>')
    selectors = ''
    for gid, name, members in groups:
        selectors += '.bubble-root:has(#pick-'+gid+':checked) #eligibility-'+gid+'{display:block}'
        live = bubble.ordered([dict(s,top5_count=repeats.get(s['code'],0)) for s in members if s['rank'] is not None], rows)
        parts.append('<div class="cross-sector" id="eligibility-'+e(gid)+'" data-sector="'+e(name)+'"><h3>'+e(name)+'</h3><p class="cross-filter-count">TOP20 '+str(len(live))+'개 · TOP5 핵심 '+str(sum(s['rank']<=5 for s in live))+'개</p>')
        for core in (True, False):
            if not core:
                parts.append('<details class="cross-aux"><summary>TOP20 보조 관찰</summary>')
            for s in live:
                if (s['rank'] <= 5) != core:
                    continue
                previous = s.get('previous_rank')
                membership = '유지' if previous is not None and previous <= 20 else '진입' if frame['index'] else '이전 관측 없음'
                parts.append(card(s,rows.get(s['code']),membership=membership))
            if not core:
                parts.append('</details>')
        parts.append('</div>')
    parts.append('</section>')
    start = html.index('<section class="cross-section"')
    end = html.index('</section>', start)+len('</section>')
    html = html[:start]+''.join(parts)+html[end:]
    return html.replace('</head>', '<style>'+CSS+selectors+'</style></head>', 1)


def render(bundle):
    return bubble.timeline.render(bundle['timeline'],frame_renderer=lambda f: frame_html(f,bundle)).replace(
        '<title>STOCK1 · Bubble Radar 장중 타임라인</title>',
        '<title>STOCK1 · 주도주 매매 적격성 검토</title>', 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-json', type=Path, required=True)
    parser.add_argument('--capital-json', type=Path)
    parser.add_argument('--proofs-json', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    def read(path):
        if path.suffix.lower() != '.json':
            parser.error('Saved JSON input only; no database input')
        return json.loads(path.read_text(encoding='utf-8-sig'))
    if args.output.suffix.lower() != '.html' or args.output.exists() or args.output.is_symlink():
        parser.error('New HTML output required')
    document = read(args.input_json)
    caps = read(args.capital_json) if args.capital_json else []
    if not isinstance(caps, list):
        caps = [caps]
    bundle = build(document, capital_records=caps, capital_proofs=read(args.proofs_json) if args.proofs_json else None)
    with args.output.open('x',encoding='utf-8') as handle:
        handle.write(render(bundle))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
