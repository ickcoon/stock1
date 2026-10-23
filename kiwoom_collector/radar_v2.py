"""Radar-only presentation and comparisons; no storage, credentials or HTTP."""
from decimal import Decimal, localcontext
from html import escape
import json
import re

from .radar import MAX_SKEW_SECONDS, number, object_json, timestamp
from .quotes import CODE


def interest_key(group):
    return (-group['interest_count'], -group['top5'], -group['top10'], group['name'])


def members(run):
    """A code is counted once in the market, once in each assigned sector."""
    unique = {}
    for stock in sorted(run['stocks'], key=lambda s: s['rank']):
        code = stock['stk_cd']
        if 1 <= stock['rank'] <= 20 and isinstance(code, str) and CODE.fullmatch(code):
            unique.setdefault(code, stock)
    return list(unique.values())


def selected_batch(stocks, mode, recorded_batches=()):
    batches = {}
    for stock in stocks:
        for q in stock['observations']:
            if (q.get('api_id') == 'ka10095' and q.get('test_mode') == mode
                    and q.get('batch_test_mode') == mode):
                at = timestamp(q.get('batch_started_at'))
                if at:
                    batches[q['batch_id']] = (at, 0)
    for sequence, batch in enumerate(recorded_batches, 1):
        at = timestamp(batch.get('started_at'))
        if batch.get('api_id') == 'ka10095' and batch.get('test_mode') == mode and at:
            batches[batch['id']] = (at, sequence)
    # Never backfill holes in the newest batch with older attempts.
    return max(batches, key=lambda key: (batches[key], key)) if batches else None


def cash_assessment(q):
    if q is None:
        return None, '시세 미수집'
    if q.get('api_id') != 'ka10095':
        return None, 'ka10095 외 관측'
    if q.get('status') not in ('success', 'partial'):
        return None, '시세 수집 실패/미완료'
    if q.get('batch_status') not in ('success', 'partial'):
        return None, '배치 미완료'
    if (q.get('delta_seconds') is None or q.get('api_delta_seconds') is None
            or abs(q['delta_seconds']) > MAX_SKEW_SECONDS
            or abs(q['api_delta_seconds']) > MAX_SKEW_SECONDS
            or any('시점 불일치' in w or '시각 미검증' in w for w in q['warnings'])):
        return None, '시점 미검증/불일치'
    norm = object_json(q.get('normalized_json'))
    # v1's source-unit declaration is NOT an independent real-unit verification.
    # No collector currently writes these two evidence fields. Never infer them.
    if (norm.get('turnover_unit_status') != 'verified'
            or not isinstance(norm.get('turnover_unit_evidence'), str)
            or not norm['turnover_unit_evidence'].strip()):
        return None, '거래대금 단위 미검증'
    amount = number(q.get('turnover_won'))
    if amount is None or amount < 0:
        return None, '거래대금 필드/값 미검증'
    return amount, None


def summarize(run):
    stocks = members(run)
    mode = run['test_mode']
    batch = selected_batch(stocks, mode, run.get('quote_batches', ()))
    viewed = []
    for stock in stocks:
        same_mode = [q for q in stock['observations']
                     if q.get('test_mode') == mode and q.get('batch_test_mode') == mode
                     and (q.get('api_id') != 'ka10095' or q.get('batch_id') == batch)]
        # Rate assessment preserves v1 assess(), with the additional mode boundary.
        rate = next((q for q in same_mode if q['eligible']), None)
        cash_quotes = [q for q in same_mode if q.get('batch_id') == batch and q.get('api_id') == 'ka10095']
        cash_quotes.sort(key=lambda q: (timestamp(q.get('request_finished_at')) is not None,
                                       timestamp(q.get('request_finished_at')) or timestamp(run['collected_at']),
                                       q.get('attempt', 0)), reverse=True)
        cash_quote = cash_quotes[0] if cash_quotes else None
        amount, reason = cash_assessment(cash_quote)
        viewed.append(dict(stock, valid_quote=rate, cash_quote=cash_quote,
                           cash=amount, cash_reason=reason))
    times = [timestamp(s['cash_quote'].get('request_finished_at')) for s in viewed if s['cash_quote']]
    api_times = [timestamp(s['cash_quote'].get('api_at')) for s in viewed if s['cash_quote']]
    coherent = all(not ts or (all(ts) and (max(ts)-min(ts)).total_seconds() <= MAX_SKEW_SECONDS)
                   for ts in (times, api_times))
    if not coherent:
        for stock in viewed:
            stock.update(cash=None, cash_reason='배치 내 관측 시간대 불일치')

    def totals(selected):
        amounts = [s['cash'] for s in selected if s['cash'] is not None]
        with localcontext() as context:
            context.prec = 100
            total = sum(amounts, Decimal(0)) if amounts else None
            mean = total / len(amounts) if amounts else None
        return dict(turnover_won=None if total is None else str(total),
                    average_turnover_won=None if mean is None else str(mean),
                    turnover_count=len(amounts), coverage_percent=100*len(amounts)/len(selected) if selected else None)

    groups = {}
    for stock in viewed:
        names = dict.fromkeys(t['sector_name'] for t in stock['tags']) or {'미분류': None}
        for name in names:
            groups.setdefault(name, []).append(stock)
    sectors = []
    for name, selected in groups.items():
        valid = [s['valid_quote'] for s in selected if s['valid_quote']]
        sectors.append(dict(name=name, stocks=selected, interest_count=len(selected),
                            top5=sum(s['rank'] <= 5 for s in selected),
                            top10=sum(s['rank'] <= 10 for s in selected),
                            up=sum(q['direction'] == 'up' for q in valid), valid_count=len(valid),
                            down=sum(q['direction'] == 'down' for q in valid),
                            flat=sum(q['direction'] == 'flat' for q in valid),
                            excluded=len(selected)-len(valid),
                            turnover_reasons=list(dict.fromkeys(s['cash_reason'] for s in selected if s['cash_reason'])),
                            **totals(selected)))
    unidentified = sum(not isinstance(s['stk_cd'], str) or CODE.fullmatch(s['stk_cd']) is None
                       for s in run['stocks'] if 1 <= s['rank'] <= 20)
    market = dict(unique_count=len(viewed), unidentified_count=unidentified, **totals(viewed))
    cash_order = bool(viewed) and not unidentified and market['turnover_count'] == len(viewed)
    sectors.sort(key=(lambda g: (-Decimal(g['turnover_won']), *interest_key(g))) if cash_order else interest_key)
    return dict(collection=run, stocks=viewed, sectors=sectors, market=market,
                batch_id=batch, batch_coherent=coherent, request_time_range=[min(times), max(times)] if times and all(times) else None,
                ordering='turnover' if cash_order else 'interest', turnover_ranking_confirmed=cash_order and run['environment'] == 'real')


def overview(runs):
    if not runs:
        return None
    current_run = runs[-1]
    prior = [r for r in runs[:-1] if (r['environment'], r['qry_tp'], r['test_mode']) ==
             (current_run['environment'], current_run['qry_tp'], current_run['test_mode'])]
    current = summarize(current_run)
    previous = summarize(prior[-1]) if prior else None
    old = {g['name']: g['interest_count'] for g in previous['sectors']} if previous else {}
    for group in current['sectors']:
        group['count_delta'] = group['interest_count'] - old.get(group['name'], 0) if previous else None
    before = {s['stk_cd'] for s in previous['stocks']} if previous else None
    entrants = [s for s in current['stocks'] if s['stk_cd'] not in before] if before is not None else None
    # 'Emerging' is a transparent positive count change, with no arbitrary score.
    emerging = sorted([g for g in current['sectors'] if g['name'] != '미분류'
                       and g['count_delta'] is not None and g['count_delta'] > 0],
                      key=lambda g: (-g['count_delta'], *interest_key(g)))
    leaders = [g for g in current['sectors'] if g['name'] != '미분류'][:3]
    return dict(current=current, previous=previous, entrants=entrants, emerging=emerging, leaders=leaders)


def e(value):
    return escape('—' if value is None else str(value), quote=True)


def money(value):
    if value is None:
        return '—'
    amount = Decimal(value)
    return f'{amount/Decimal(100000000):,.2f}억 원' if amount >= 100000000 else f'{amount:,.0f}원'


def delta(value):
    return '비교 대상 없음' if value is None else f'{value:+d}종목'


def clock_label(value):
    at = timestamp(value)
    return at.strftime('%H:%M:%S') if at else '—'


def rate_label(stock):
    q = stock['valid_quote']
    if not q:
        return '등락률 미확인'
    return {'up': '상승', 'down': '하락', 'flat': '보합'}[q['direction']] + ' ' + q['change_percent'] + '%'


def spread_label(group):
    if not group['valid_count']:
        return '시세 미확인'
    return f"상승 {group['up']} / 유효 {group['valid_count']} · 전체 {group['interest_count']}"


def turnover_label(group, environment):
    count, total = group['turnover_count'], group['interest_count']
    if not count:
        return '거래대금 미검증'
    if count < total:
        return f'거래대금 일부 검증 {count}/{total}'
    return '거래대금 모의 검증' if environment == 'mock' else '거래대금 검증 완료'


def stock_details(stock):
    """Keep diagnostics inside native details; whitelist raw market numeric fields."""
    parts = [f"<details><summary>{e(stock['rank'])}위 · {e(stock['stk_nm'])} · {e(rate_label(stock))}</summary>",
             f"<p>{e(stock['stk_cd'])} · 거래대금 {e(money(stock['cash']))} · {e(stock['cash_reason'])}</p>"]
    if not stock['observations']:
        parts.append('<p>시세 미수집</p>')
    for q in stock['observations']:
        raw = {k: v for k, v in object_json(q.get('raw_json')).items()
               if k in ('cur_prc', 'flu_rt', 'trde_prica', 'trde_qty', 'date', 'tm', 'dt', 'cntr_tm', 'bid_tm')
               and isinstance(v, str) and len(v) <= 64 and re.fullmatch(r'[+\-0-9. ]*', v)}
        parts.extend([f"<p>{e(q.get('api_id'))} · {e(q.get('status'))} · test_mode={e(q.get('test_mode'))}",
                      f"<br>배치 {e(q.get('batch_id'))} · 시도 {e(q.get('attempt', 0))}",
                      f"<br>요청 {e(q.get('request_started_at'))} → {e(q.get('request_finished_at'))}",
                      f"<br>API {e(q.get('api_at'))} · 순위 대비 요청 {e(q['delta_seconds'])}초 / API {e(q['api_delta_seconds'])}초",
                      f"<br>{e(' · '.join(q['warnings']) or '필드 검증 통과')}<br>거래대금 단위의 독립 검증은 별도 조건입니다.</p>",
                      '<details><summary>원본 시장 필드 / 정규화 진단</summary><pre>' + e(json.dumps(dict(
                          raw=raw, change_percent=q['change_percent'], turnover_won=q['turnover_won'],
                          eligible=q['eligible'], candidate=q['candidate'], issues=q.get('issues_json')), ensure_ascii=False, indent=2)) + '</pre></details>'])
    return ''.join(parts) + '</details>'


STYLE = '''
:root{color-scheme:light;--ink:#213047;--muted:#617087;--line:#e2e8ef;--blue:#245aa7;--green:#196447}
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:var(--ink);font:14px/1.4 system-ui,"Malgun Gothic",sans-serif}
main{max-width:1440px;margin:auto;padding:18px}h1{font-size:24px;margin:0}h2{font-size:16px;margin:0 0 8px}h3{font-size:15px;margin:0 0 5px}
p{margin:5px 0}header{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:8px}.muted,small{color:var(--muted)}small{display:block}
.badge{display:inline-block;border:1px solid var(--line);border-radius:5px;padding:2px 8px;font-size:12px;background:#fff}.blue{color:var(--blue)}.green{color:var(--green)}
.notice{background:#fff8e9;border:1px solid #ebd9ac;border-radius:9px;padding:8px 12px;margin:9px 0}.kpis{display:grid;grid-template-columns:1.7fr 1.1fr .7fr;gap:10px}
.card,section{min-width:0;background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px}.kpis ol{padding-left:22px;margin:0}.kpis li{margin:4px 0}.big{font-size:28px;line-height:1.2;font-weight:650}.big small{font-size:12px;font-weight:400;line-height:1.4;margin-top:4px}.cash-status{font-size:12px;color:var(--muted)}
.layout{display:grid;grid-template-columns:minmax(0,2.9fr) minmax(260px,1fr);gap:10px;margin-top:10px}table{width:100%;border-collapse:collapse;font-size:13px;line-height:1.35}th{text-align:left;color:var(--muted);font-weight:550;background:#f8fafc}th,td{padding:7px;border-bottom:1px solid var(--line);vertical-align:top}.scroll{overflow:auto;max-height:430px}.scroll>table{min-width:740px}.entrants{max-height:430px;overflow:auto}td.num,th.num{text-align:right;white-space:nowrap}
.sector-name{font-size:14px;font-weight:650}.rep{font-size:13px;color:#526176;margin-top:3px}.rise{color:var(--green);font-weight:600}.popular{color:var(--blue)}.candidate{color:var(--green)}.warning{color:#8a5916}
details{margin-top:8px}summary{cursor:pointer;color:var(--blue);padding:3px 0}.member-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 10px}.members{margin-top:0}.member-meta .members[open]{flex-basis:100%}.members summary{padding:1px 0;font-size:12px}pre,code{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}details section{margin-top:8px}.stock{padding:8px 0;border-bottom:1px solid var(--line)}.foot{font-size:12px;color:var(--muted);margin-top:9px}.timing{font-size:12px;color:var(--muted);margin-top:4px}.unclassified{margin-top:10px;padding:8px 12px}.unclassified details{display:inline-block;vertical-align:top;margin:0 0 0 16px}.unclassified li{margin:4px 0}
@media(max-width:850px){main{padding:12px}.layout{grid-template-columns:minmax(0,1fr)}.kpis{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.kpis .card:last-child{grid-column:1/-1}.kpis .big{font-size:24px}header{align-items:flex-start;flex-direction:column;gap:6px}}
'''


def render(report):
    data = report.get('overview') if 'overview' in report else overview(report['runs'])
    parts = ['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>주도섹터 레이더 v2</title><style>'+STYLE+'</style></head><body><main>',
             '<header><div><h1>주도섹터 레이더 <span class="muted">v2</span></h1><p class="muted">시장 관심도 · 거래 집중도 · 상승 확산도</p></div>',
             f"<div><span class='badge'>{e(report['date'])}</span> <span class='badge'>{e(report['environment'])} {'모의 데이터' if report['environment']=='mock' else '실전 데이터'}</span></div></header>"]
    if data is None:
        return ''.join(parts) + '<section>선택 날짜·환경에 성공한 순위 수집이 없습니다.</section></main></body></html>'
    current, previous = data['current'], data['previous']
    run = current['collection']
    cash = current['ordering'] == 'turnover'
    title = '주도섹터 TOP 3 · 거래대금 기준' if current['turnover_ranking_confirmed'] else '관심섹터 TOP 3 · 주도 판단 보류'
    if cash and report['environment'] == 'mock':
        title = '모의 섹터 TOP 3 · 거래대금 기준'
    parts += [f"<p class='timing'>현재 {clock_label(run['collected_at'])} · 직전 {clock_label(previous['collection']['collected_at']) if previous else '비교 대상 없음'} (KST) · 조회유형 {e(run['qry_tp'])} · {'테스트' if run['test_mode'] else '운영'} 회차</p>",
              '<div class="notice">'+('거래대금 단위·값·회차·시점 검증 및 전체 종목 커버리지 충족. ' if cash else '거래대금 기준 순위 확정 보류 · 단위 미검증 또는 커버리지 부족. 관심도순으로 표시합니다. ')+
              '<b class="blue">조회순위상 인기</b>와 <b class="green">시세 상승이 확인된 주도주 후보</b>를 구분합니다. 매수 신호가 아닙니다.</div>',
              '<div class="kpis"><div class="card"><h2>'+title+'</h2><ol>']
    for group in data['leaders']:
        parts.append(f"<li><b>{e(group['name'])}</b> · TOP20 {group['interest_count']}종목 · <span class='cash-status'>{turnover_label(group, report['environment'])}</span></li>")
    if not data['leaders']:
        parts.append('<li>분류된 섹터 없음</li>')
    parts += ['</ol></div><div class="card"><h2>직전 대비 부상 · 관심도 기준</h2>']
    parts += [f"<p><b>{e(g['name'])}</b> <span class='rise'>{delta(g['count_delta'])}</span></p>" for g in data['emerging'][:3]]
    if not data['emerging']:
        parts.append('<p class="muted">'+('증가 섹터 없음' if previous else '비교 대상 없음')+'</p>')
    parts += ['</div><div class="card"><h2>TOP20 신규 진입</h2>',
              f"<div class='big'>{len(data['entrants']) if data['entrants'] is not None else '—'}<small>{'종목 / 전체 '+str(current['market']['unique_count'])+'종목' if previous else '비교 대상 없음'}</small></div></div></div>",
              '<div class="layout"><section><h2>섹터별 세 가지 판단 근거</h2><div class="scroll"><table><thead><tr><th>섹터 / 대표 종목</th><th class="num">거래대금 / 평균</th><th class="num">TOP20</th><th class="num">상승 확산</th><th class="num">직전 대비</th></tr></thead><tbody>']
    for group in current['sectors']:
        if group['name'] == '미분류':
            continue
        all_members = ''.join(f"<p>{s['rank']}위 · {e(s['stk_nm'])} · {e(rate_label(s))}</p>" for s in group['stocks'])
        parts += [f"<tr><td><span class='sector-name'>{e(group['name'])}</span><div class='rep'>{e(' · '.join(s['stk_nm'] or s['stk_cd'] for s in group['stocks'][:3]))}</div><div class='member-meta'><small>TOP5 {group['top5']} · TOP10 {group['top10']}</small><details class='members'><summary>전체 {group['interest_count']}종목</summary>{all_members}</details></div></td>",
                  f"<td class='num'>{e(money(group['turnover_won']))}<small>평균 {e(money(group['average_turnover_won']))}</small><small>적용 {group['turnover_count']}/{group['interest_count']} · {group['coverage_percent']:.0f}%{' · 부분합' if group['turnover_count'] < group['interest_count'] and group['turnover_count'] else ''}</small>"+
                  (f"<small>{e(group['turnover_reasons'][0])}</small>" if group['turnover_reasons'] else '')+'</td>',
                  f"<td class='num'>{group['interest_count']}</td><td class='num'>{e(spread_label(group))}"+
                  (f"<small>하락 {group['down']} · 보합 {group['flat']}</small>" if group['valid_count'] else '')+
                  f"</td><td class='num'>{delta(group['count_delta'])}</td></tr>"]
    if not any(g['name'] != '미분류' for g in current['sectors']):
        parts.append('<tr><td colspan="5">분류된 섹터 없음 · 미분류 종목은 아래에서 확인하세요.</td></tr>')
    parts += ['</tbody></table></div>', f"<p class='foot'>TOP20 고유 {current['market']['unique_count']}종목 · 중복 제거 거래대금 {e(money(current['market']['turnover_won']))} · 적용 {current['market']['turnover_count']}/{current['market']['unique_count']}종목. TOP20 표본이며 시장 전체 거래대금이 아닙니다.</p>",
              '</section><section><h2>신규 진입 종목</h2><div class="entrants">']
    if data['entrants'] is None:
        parts.append('<p class="muted">비교 대상 없음</p>')
    elif not data['entrants']:
        parts.append('<p class="muted">신규 진입 없음</p>')
    else:
        for stock in data['entrants']:
            names = ' · '.join(dict.fromkeys(t['sector_name'] for t in stock['tags'])) or '미분류'
            parts.append(f"<div class='stock'><span class='badge'>신규</span> <b>{e(stock['stk_nm'])}</b> · {stock['rank']}위<small>{e(names)} · {e(rate_label(stock))}</small></div>")
    unclassified = next((g['stocks'] for g in current['sectors'] if g['name'] == '미분류'), [])
    parts += ['</div></section></div>',
              f'<section class="unclassified"><b>미분류 {len(unclassified)}종목</b><details><summary>미분류 종목 목록 보기</summary>']
    if unclassified:
        parts.append('<ul>'+''.join(f"<li>{s['rank']}위 · {e(s['stk_nm'])} ({e(s['stk_cd'])}) · {e(rate_label(s))}</li>" for s in unclassified)+'</ul>')
    else:
        parts.append('<p>미분류 종목 없음</p>')
    parts += ['</details></section><details><summary>전체 종목 · 시세 검증 상세</summary>']
    for stock in current['stocks']:
        parts.append(stock_details(stock))
    parts += ['</details><details><summary>비교 기준 · 집계 정책 · 회차 진단</summary>',
              '<p>동일 KST 날짜·환경·qry_tp·test_mode의 성공 회차만 실제 수집 시각순으로 비교합니다. 시각이 같으면 저장 순서로 결정합니다. 현재 수동 태그가 과거 회차에도 적용되며 과거 태그 이력을 재현하지 않습니다.</p>',
              '<p>관심도 정렬: 종목 수 → TOP5 → TOP10 → 섹터명. 거래대금은 같은 회차의 최신 단일 ka10095 배치만 사용하며 여러 배치·시간대의 누적액을 합치지 않습니다. 같은 배치도 순차 요청이며 정확한 동시 스냅샷이 아닙니다. 복수 섹터는 각각 포함하고 TOP20 전체 합계는 코드로 중복 제거합니다. 미분류는 TOP 3·부상 순위·섹터 표에서 제외하고 하단 목록으로 분리합니다. TOP20 전체 수와 거래대금 커버리지의 분모에는 유지합니다.</p>',
              '<p>단위 독립 검증·시점·필드 검증을 통과한 종목만 거래대금 부분합/적용 종목당 평균을 표시합니다. 0원과 미검증 —는 다릅니다. 모든 TOP20 종목이 검증되어야 거래대금순으로 정렬합니다. 모의 값은 실전 검증을 증명하지 않습니다.</p>',
              '<p>상승/하락/보합은 기존 assess()의 등락률 검증과 ±300초·같은 날짜 규칙을 사용합니다. 시세 실패·미수집은 상승/보합으로 세지 않습니다. 현재가 부호는 미확정 상태를 유지하며 거래대금 단위 검증과 별개입니다. 과거 시세를 현재처럼 채우지 않습니다.</p>',
              f"<p>현재 UUID {e(run['id'])}<br>직전 UUID {e(previous['collection']['id']) if previous else '—'}<br>코드 미확인 순위 {current['market']['unidentified_count']}건<br>시세 배치 {e(current['batch_id'])}<br>요청 완료 범위 {e(current['request_time_range'])}<br>시간대 일관성 {e(current['batch_coherent'])}</p></details>",
              '<details><summary>v1 상세 진단 · 선택 날짜의 모든 성공 회차</summary>']
    # Preserve every v1 observation/diagnostic without nested HTML documents.
    from .radar import render_diagnostics
    legacy = render_diagnostics(report).split('<main>', 1)[1].split('</main>', 1)[0]
    parts += [legacy, '</details><p class="foot">로컬 읽기 전용 · 외부 리소스 없음 · 자동 주문 없음</p></main></body></html>']
    return ''.join(parts)
