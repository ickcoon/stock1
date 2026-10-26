"""Standalone, read-only Sector Bubble Radar v1 built on Radar v3.

python -m kiwoom_collector.sector_bubble --date YYYY-MM-DD --output new.html
"""
import argparse
from hashlib import sha256
import math
from pathlib import Path
import sqlite3
import json
from datetime import datetime

from .client import KST
from .radar import timestamp
from . import radar_v3
from .radar_v2 import e, money, rate_label, spread_label, turnover_label, STYLE


MAX_BUBBLES = 8
COLORS = {'increase': '#b94243', 'decrease': '#3269aa',
          'steady': '#667282', 'unknown': '#87919b'}


def identity(name):
    return 's'+sha256(name.encode('utf-8')).hexdigest()[:16]


def change_state(group, comparable):
    delta = group['delta20']
    if delta is None or not comparable:
        return 'unknown'
    return 'increase' if delta > 0 else 'decrease' if delta < 0 else 'steady'


def layout(groups, *, mobile=False):
    """Fixed cells, one shared radius scale: every circle area is k²*pi*count.

    The common scale fits radii in [22, 102] viewBox units for TOP20 values
    1..20. Never clamp individual values, which would break area ratios.
    Name hashes allocate cells independently of count or delta. Collision
    resolution is deterministic; changing the selected set can move a cell.
    """
    groups = [g for g in groups if g['count'] > 0 and g['name'] != '미분류']
    if not groups:
        return dict(width=440 if mobile else 960, height=250, nodes=[], scale=0)
    if len(groups) > MAX_BUBBLES or any(not 1 <= g['count'] <= 20 for g in groups):
        raise ValueError('지도는 1~20종목인 최대 8섹터를 표시합니다.')
    columns = 2 if mobile else 4
    # Fixed cells reserve space even when a sector disappears.
    capacity = MAX_BUBBLES
    width, cell = (440,220) if mobile else (960,240)
    height = cell * math.ceil((len(groups) if mobile else capacity) / columns)
    max_radius = 100 if mobile else 102
    scale = min(62, max_radius/math.sqrt(max(g['count'] for g in groups)))
    occupied = set()
    nodes = []
    for index,group in enumerate(sorted(groups, key=lambda g: sha256(g['name'].encode('utf-8')).hexdigest())):
        home = int(sha256(group['name'].encode('utf-8')).hexdigest()[:8],16) % capacity
        slot = index if mobile else next((home+i) % capacity for i in range(capacity) if (home+i) % capacity not in occupied)
        occupied.add(slot)
        nodes.append(dict(name=group['name'], id=identity(group['name']), slot=slot,
                          x=cell*(slot % columns)+cell/2, y=cell*(slot//columns)+cell/2,
                          r=scale*math.sqrt(group['count']), count=group['count']))
    return dict(width=width,height=height,nodes=nodes,scale=scale)


def model(data):
    current, previous = data['current'], data['previous']
    current_stocks = {s['stk_cd']:s for s in current['stocks']} if current else {}
    old_stocks = {s['stk_cd']:s for s in previous['stocks']} if previous else {}
    groups = []
    for g in data['groups']:
        item = dict(g, id=identity(g['name']), state=change_state(g,data['comparison_complete']))
        before_count = g['count']-g['delta20'] if g['delta20'] is not None else None
        item['new_sector'] = bool(item['state'] == 'increase' and before_count == 0)
        old_sector_codes = {code for code,s in old_stocks.items() if any(t['sector_name'] == g['name'] for t in s['tags'])}
        now_sector_codes = {s['stk_cd'] for s in g['current']['stocks']} if g['current'] else set()
        stocks = []
        for code in now_sector_codes | old_sector_codes:
            now, old = current_stocks.get(code), old_stocks.get(code)
            status = ('비교 불가' if previous is None else '유지' if code in now_sector_codes & old_sector_codes
                      else '신규 진입' if code in now_sector_codes else '이탈')
            stocks.append(dict(code=code,name=(now or old)['stk_nm'],
                               rank=now['rank'] if now else None, previous_rank=old['rank'] if old else None,
                               status=status, rate=rate_label(now or old),
                               rate_scope='현재 관측' if now else '직전 관측',
                               cash_reason=(now or old).get('cash_reason') or '검증된 거래대금',
                               cash=(now or old).get('cash')))
        stocks.sort(key=lambda s: (s['rank'] is None,s['rank'] or s['previous_rank'] or 99,s['code']))
        item['members'] = stocks
        groups.append(item)
    positive = sorted([g for g in groups if g['count'] > 0],
                      key=lambda g: (-g['count'],-g['top5'],g['name']))
    selected, remainder = positive[:MAX_BUBBLES], positive[MAX_BUBBLES:]
    return dict(groups=groups,selected=selected,remainder=remainder,
                exits=[g for g in groups if g['count']==0 and g['delta20'] is not None and g['delta20']<0],
                historical=[g for g in groups if g['count']==0 and not (g['delta20'] is not None and g['delta20']<0)],
                desktop=layout(selected),mobile=layout(selected,mobile=True))


def delta_label(value):
    return '비교 불가' if value is None else f'{value:+d}' if value else '0'


CSS = '''
:root{--ink:#26394d;--muted:#65778a;--line:#dce5ed;--red:#b94243;--blue:#3269aa}
body{background:#f2f5f8;font:14px/1.45 system-ui,"Malgun Gothic",sans-serif;color:var(--ink)}
.bubble-root{max-width:1440px;margin:auto;padding:18px}.bubble-root h1{font-size:23px;letter-spacing:-.6px}
.bubble-root h2{font-size:16px;margin:0}.subtitle{font-size:12px;color:var(--muted);margin-top:3px}
.bubble-head{margin-bottom:8px}.brief{display:flex;flex-wrap:wrap;gap:8px 24px;padding:9px 14px;border:1px solid var(--line);border-radius:10px;background:#fff;margin:10px 0}
.brief b{font-size:18px}.brief span{font-size:12px;color:var(--muted)}
.meaning{font-size:12px;background:#fff8ec;padding:8px 12px;border:1px solid #f0dfbd;border-radius:8px}
.view-controls{display:flex;align-items:center;gap:5px;margin:12px 0 9px;flex-wrap:wrap}
.view-controls label{cursor:pointer;border:1px solid var(--line);padding:6px 13px;border-radius:7px;background:#fff;font-weight:600;font-size:12px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.bubble-root:has(#mode-map:checked) label[for=mode-map],.bubble-root:has(#mode-table:checked) label[for=mode-table],.bubble-root:has(#mode-both:checked) label[for=mode-both]{background:#26394d;color:#fff;border-color:#26394d}
.bubble-root:has(#mode-map:focus-visible) label[for=mode-map],.bubble-root:has(#mode-table:focus-visible) label[for=mode-table],.bubble-root:has(#mode-both:focus-visible) label[for=mode-both]{outline:3px solid #e6ac46;outline-offset:2px}
.bubble-layout{display:grid;grid-template-columns:minmax(0,2.65fr) minmax(300px,1fr);gap:12px;align-items:start}
.map-card{padding:14px 16px 10px;min-width:0;background:#fff;border:1px solid var(--line);border-radius:12px}
.map-heading{display:flex;justify-content:space-between;gap:10px;align-items:baseline}.map-heading small{font-size:11px}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 0;font-size:11px;color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px}.map-frame{position:relative}
.bubble-svg{display:block;width:100%;height:auto;overflow:visible}.mobile-map{display:none}
.desktop-map{max-height:420px}
.bubble-circle{stroke-width:2;stroke:#fff}.bubble-label{display:flex;width:100%;height:100%;flex-direction:column;justify-content:center;align-items:center;text-align:center;color:#fff;cursor:pointer;border-radius:50%;overflow:hidden;padding:6px;line-height:1.25;box-sizing:border-box}
.bubble-label .name{font-size:15px;font-weight:650;max-width:95%;overflow-wrap:anywhere}.bubble-label .count{font-size:28px;font-weight:700;margin:3px 0 1px;font-variant-numeric:tabular-nums}
.bubble-label .change{font-size:12px}.bubble-label .representative{font-size:10px;opacity:.9;margin-top:4px;max-width:90%;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.bubble-label.tiny .name{font-size:11px}.bubble-label.tiny .count{font-size:16px}.bubble-label.tiny{padding:3px}
.bubble-label .new{font-size:9px;font-weight:700;letter-spacing:1px}.bubble-circle.unknown{stroke:#526172;stroke-dasharray:4 3}
.map-key{margin-top:3px;border-top:1px solid #edf1f5;padding-top:8px;display:flex;flex-wrap:wrap;gap:5px}
.sector-chip{font-size:11px;display:inline-block;border:1px solid var(--line);border-radius:5px;padding:3px 7px;cursor:pointer;background:#f9fbfd;max-width:100%;overflow-wrap:anywhere}
.outside{font-size:11px;color:var(--muted);margin-top:8px}.outside label{margin:2px}.map-foot{font-size:11px;color:var(--muted);margin-top:7px}
.sidebar{display:grid;gap:10px}.summary-card,.detail-card{padding:12px;border:1px solid var(--line);border-radius:12px;background:#fff;min-width:0}
.summary-cols{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px}.summary-cols p{margin:3px 0}.repeat-line{font-size:11px;border-top:1px solid #edf1f5;padding-top:6px;margin-top:6px}
.detail-card h2{margin-bottom:8px}.detail-scroll{max-height:322px;overflow:auto;overscroll-behavior:contain}.sector-panel{display:none}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;margin:8px 0}.detail-grid div{border:1px solid #e5ebf0;background:#f7f9fc;border-radius:5px;padding:5px 7px}.detail-grid small{font-size:10px}.detail-status{font-size:11px;color:var(--muted)}
.member-table{font-size:11px;table-layout:fixed;width:100%}.member-table th,.member-table td{padding:5px 3px;overflow-wrap:anywhere}.member-table th:first-child{width:40%}.member-table small{font-size:10px}
.bubble-footer{padding:9px 12px;margin-top:10px;background:#fff;border:1px solid var(--line);border-radius:8px;font-size:12px}
.legacy-panel{display:none;margin-top:16px}.bubble-root:has(#mode-table:checked) .bubble-layout{display:none}.bubble-root:has(#mode-table:checked) .legacy-panel,.bubble-root:has(#mode-both:checked) .legacy-panel{display:block}
.bubble-root:has(#mode-table:checked) .bubble-footer{display:none}.no-js-note{font-size:11px;color:var(--muted)}
@supports not selector(:has(*)){.sector-panel,.legacy-panel{display:block}.view-controls{display:none}}
@media(max-width:850px){.bubble-root{padding:12px}.bubble-layout{grid-template-columns:1fr}.sidebar{grid-template-columns:1fr 1fr}.detail-scroll{max-height:400px}}
@media(max-width:540px){.mobile-map{display:block}.desktop-map{display:none}.sidebar{grid-template-columns:1fr}.map-card{padding:12px}.bubble-head{align-items:flex-start}.bubble-root h1{font-size:21px}.brief{gap:5px 14px}.brief b{font-size:16px}.detail-scroll{max-height:none}.map-heading{flex-direction:column;gap:2px}}
'''


def svg_map(geometry, by_name, mobile=False):
    parts = [f'<svg class="bubble-svg {"mobile-map" if mobile else "desktop-map"}" viewBox="0 0 {geometry["width"]} {geometry["height"]}" role="group" aria-label="섹터 관심도 버블맵">']
    for node in geometry['nodes']:
        g = by_name[node['name']]
        x,y,r = node['x'],node['y'],node['r']
        tiny = r < 44
        # Very small marks use an abbreviated label; the full name remains in
        # the accessible label, native tooltip, chip list and selected panel.
        name = g['name'] if not tiny or len(g['name'])<=3 else g['name'][:2]+'…'
        summary = f'{g["name"]} · TOP20 {g["count"]} · 직전 {delta_label(g["delta20"])}'
        parts += [f'<g class="bubble-node node-{g["id"]}" data-sector="{e(g["name"])}" data-count="{g["count"]}" data-state="{g["state"]}"><title>{e(summary)}</title>',
                  f'<circle class="bubble-circle {g["state"]}" cx="{x}" cy="{y}" r="{r:.8f}" fill="{COLORS[g["state"]]}"/>',
                  f'<foreignObject x="{x-r:.8f}" y="{y-r:.8f}" width="{2*r:.8f}" height="{2*r:.8f}"><label xmlns="http://www.w3.org/1999/xhtml" class="bubble-label {"tiny" if tiny else ""}" for="pick-{g["id"]}" title="{e(summary)}">',
                  '<span class="new">신규</span>' if g['new_sector'] and not tiny else '',
                  f'<span class="name">{e(name)}</span><span class="count">{g["count"]}</span>',
                  f'<span class="change">{e(delta_label(g["delta20"]))}</span>' if not tiny else '']
        if r >= 80 and g['current'] and g['current']['stocks']:
            parts.append('<span class="representative">'+e(g['current']['stocks'][0]['stk_nm'])+'</span>')
        parts.append('</label></foreignObject></g>')
    parts.append('</svg>')
    return ''.join(parts)


def panel(group, data):
    g, c = group, group['current']
    d = '비교 불가' if g['delta20'] is None else delta_label(g['delta20'])
    parts = [f'<article class="sector-panel" id="detail-{g["id"]}" aria-labelledby="heading-{g["id"]}"><h2 id="heading-{g["id"]}">{e(g["name"])}</h2>',
             '<div class="detail-grid">',
             f'<div><small>현재 TOP20 / TOP5</small><b>{g["count"]} / {g["top5"]}종목</b></div>',
             f'<div><small>직전 대비 TOP20 / TOP5</small><b>{e(d)} / {e(delta_label(g["delta5"]))}</b></div>',
             f'<div><small>당일 성공 관측 출현</small><b>{g["appearances"]}/{g["observations"]}회</b></div>',
             f'<div><small>관측 연속 / TOP5 출현</small><b>{g["streak"]} / {g["top5_appearances"]}회</b></div></div>']
    if not data.get('comparison_complete') and data['previous']:
        parts.append('<p class="detail-status">불완전 표본: 변화는 관측 수 차이이며 지도 색상 판단은 보류합니다.</p>')
    if c:
        parts.append(f'<p class="detail-status">{e(spread_label(c))} · {e(turnover_label(c,data["environment"]))}<br>거래대금 {e(money(c["turnover_won"]))} · 평균 {e(money(c["average_turnover_won"]))} · 적용 {c["turnover_count"]}/{c["interest_count"]}'+(' · 부분합' if 0<c['turnover_count']<c['interest_count'] else '')+'</p>')
    else:
        parts.append('<p class="detail-status">현재 TOP20에 없음 · 원은 표시하지 않습니다. 아래 시세는 직전 관측입니다.</p>')
    parts.append('<table class="member-table"><thead><tr><th>종목 / 상태</th><th>현재</th><th>직전</th><th>시세 검증</th></tr></thead><tbody>')
    for s in g['members']:
        parts.append(f'<tr><td><b>{e(s["name"] or s["code"])}</b><small>{e(s["code"])} · {s["status"]}</small></td><td>{s["rank"] or "—"}</td><td>{s["previous_rank"] or "—"}</td><td>{e(s["rate"])}<small>{s["rate_scope"]} · {e(s["cash_reason"])} · {e(money(s["cash"]))}</small></td></tr>')
    return ''.join(parts)+'</tbody></table></article>'


def render(data):
    m = model(data)
    by_name = {g['name']:g for g in m['groups']}
    selectable = m['selected']+m['remainder']+m['exits']+m['historical']
    selectors = ''.join(f'.bubble-root:has(#pick-{g["id"]}:checked) #detail-{g["id"]}'+'{display:block}'+
                        f'.bubble-root:has(#pick-{g["id"]}:checked) .node-{g["id"]} circle'+'{stroke:#1e2d40;stroke-width:4}'+
                        f'.bubble-root:has(#pick-{g["id"]}:focus-visible) .node-{g["id"]} circle'+'{stroke:#e5aa37;stroke-width:5}'+
                        f'.bubble-root:has(#pick-{g["id"]}:checked) label[for="pick-{g["id"]}"].sector-chip'+'{background:#e7eef6;border-color:#45688d}' for g in selectable)
    cur,prev = data['current'],data['previous']
    parts = ['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>STOCK1 · Sector Bubble Radar v1</title><style>'+STYLE+radar_v3.V3_STYLE+CSS+selectors+'</style></head><body><main class="bubble-root">',
             '<header class="bubble-head"><div><h1>Sector Bubble Radar <span class="muted">v1</span></h1><p class="subtitle">관심의 크기 · 직전 관측 변화 · 당일 반복</p></div>',
             f'<div><span class="badge">{e(data["date"])}</span> <span class="badge">{e(data["environment"])} · {"모의" if data["environment"]=="mock" else "실전"}</span> <span class="badge">qry_tp=1 · {"테스트" if data["test_mode"] else "운영"}</span></div></header>']
    if cur:
        at=timestamp(cur['collection']['collected_at']).strftime('%H:%M')
        old=timestamp(prev['collection']['collected_at']).strftime('%H:%M') if prev else '없음'
        gap=f'{data["gap_seconds"]/60:g}분' if prev else '비교 불가'
        total=cur['market']['unique_count']
        unclassified=sum(not s['tags'] for s in cur['stocks'])
        parts += [f'<div class="brief"><div><span>현재 / 직전 성공 KST</span> <b>{at} / {old}</b></div><div><span>실제 간격</span> <b>{gap}</b></div><div><span>성공 관측</span> <b>{len(data["snapshots"])}회</b></div><div><span>진입 · 유지 · 이탈</span> <b>'+(' · '.join(str(len(data[k])) for k in ('entrants','retained','exits')) if prev else '—')+'</b></div></div>']
    else:
        total=unclassified=0
    parts += ['<p class="meaning">원 면적 = 현재 TOP20 고유 종목 수 · 빨강/파랑 = 조회 관심도 증가/감소이며 <b>주가 등락률이 아닙니다.</b> 매수 신호·주도 확정이 아닙니다. 현재 태그를 과거 관측에도 적용합니다.</p>',
              '<div class="view-controls" role="group" aria-label="보기 선택">']
    for value,label in [('map','버블맵'),('table','v3 표 보기'),('both','함께 보기')]:
        parts.append(f'<input class="sr-only" type="radio" name="view" id="mode-{value}" {"checked" if value=="map" else ""}><label for="mode-{value}">{label}</label>')
    parts.append('<span class="no-js-note">로컬 HTML · 원 클릭 또는 키보드로 상세 선택</span></div>')
    for i,g in enumerate(selectable):
        parts.append(f'<input class="sr-only sector-choice" type="radio" name="sector" id="pick-{g["id"]}" aria-label="{e(g["name"])} 상세 선택" {"checked" if i==0 else ""}>')
    parts += ['<div class="bubble-layout"><section class="map-card"><div class="map-heading"><h2>섹터 관심 지도</h2>',
              f'<small>현재 {len(m["selected"])} / {len(m["selected"])+len(m["remainder"])}섹터 표시 · 크기에 지속성 가중 없음</small></div>',
              '<div class="legend" aria-label="관심도 변화 범례">'+''.join(f'<span><i style="background:{COLORS[state]}"></i>{label}</span>' for state,label in [('increase','증가 +'),('decrease','감소 −'),('steady','유지 0'),('unknown','비교 불가 · 점선')])+'<span>신규 = 직전 0 → 현재 등장</span></div>']
    if data['previous'] and not data['comparison_complete']:
        parts.append('<p class="warning compact">불완전 TOP20 표본 · 색상 판단 보류</p>')
    if data['intervening_slots']:
        gaps=[s['slot']+' '+s['state'] for s in data['intervening_slots'] if s['state']!='success']
        if gaps: parts.append('<p class="warning compact">중간 미관측/미완료: '+e(' · '.join(gaps))+'</p>')
    if m['selected']:
        parts += ['<div class="map-frame">',svg_map(m['desktop'],by_name),svg_map(m['mobile'],by_name,True),'</div>']
    else:
        parts.append('<p class="empty-map">분류된 현재 섹터 없음'+(' · 미분류는 아래 별도 안내합니다.' if cur else ' · 선택 흐름에 성공한 순위 수집이 없습니다.')+'</p>')
    def chips(groups):
        return ''.join(f'<label class="sector-chip" for="pick-{g["id"]}">{e(g["name"])} · {g["count"]}'+(' · 신규' if g['new_sector'] else '')+'</label>' for g in groups)
    parts += ['<div class="map-key">',chips(m['selected']),'</div>']
    if m['remainder']: parts.append('<div class="outside"><b>지도 밖 현재 섹터</b> '+chips(m['remainder'])+'</div>')
    if m['exits']: parts.append('<div class="outside"><b>현재 이탈 · 원 없음</b> '+chips(m['exits'])+'</div>')
    if m['historical']: parts.append('<details class="outside"><summary>이전 관측에만 등장한 섹터</summary>'+chips(m['historical'])+'</details>')
    parts += ['<p class="map-foot">동일 값은 동일 면적 · 공통 축척으로 최소/최대 크기 제한 · 배치는 순위나 추가 점수가 아닙니다. 작은 원의 전체 이름은 목록·상세에서 확인하세요.</p></section>',
              '<aside class="sidebar"><section class="summary-card"><h2>종목의 교체 · 반복</h2><div class="summary-cols">']
    for key,title in [('entrants','신규 진입'),('exits','이탈 · 직전 순위')]:
        stocks=data[key]
        parts.append(f'<div><b>{title}</b>')
        parts += [f'<p>{s["rank"]}위 · {e(s["stk_nm"] or s["stk_cd"])}</p>' for s in (stocks or [])[:2]] or [f'<p>{"비교 불가" if stocks is None else "없음"}</p>']
        if stocks and len(stocks)>2: parts.append(f'<small>외 {len(stocks)-2}종목 · v3 표에서 전체 보기</small>')
        parts.append('</div>')
    parts += ['</div><div class="repeat-line"><b>현재 반복 종목</b> · '+e(' / '.join((s['name'] or s['code'])+' '+str(s['appearances'])+'/'+str(len(data['snapshots'])) for s in data['repeated_stocks'][:2]) or '관측 없음')+'</div></section>',
              '<section class="detail-card"><h2 class="detail-intro">선택 섹터 상세</h2><div class="detail-scroll" role="region" aria-label="선택 섹터 상세">']
    parts += [panel(g,data) for g in selectable] or ['<p>선택 가능한 섹터 없음</p>']
    parts += ['</div></section></aside></div>',
              f'<div class="bubble-footer">TOP20 고유 <b>{total}</b>종목 · 미분류 <b>{unclassified}</b>종목(지도 제외) · 섹터 소속 합계 {sum(g["count"] for g in m["groups"])} · 복수 섹터 {len(data.get("overlap",[]))}종목. 섹터 합계는 전체 고유 수를 초과할 수 있습니다.<br><span class="muted">출현 횟수의 분모는 관측된 성공 회차입니다. 예정 10회 완료나 시간 점유율이 아닙니다. '+('모의 값은 실전 검증을 증명하지 않습니다.' if data['environment']=='mock' else '검증 부족 시세·거래대금은 미확인으로 유지합니다.')+'</span></div>']
    # Keep the complete existing v3 renderer and every disclosure without changing v3.
    legacy_html=radar_v3.render(data).split('<main class="v3">',1)[1].rsplit('</main>',1)[0]
    parts += ['<section class="legacy-panel v3" aria-label="기존 Radar v3 표와 검증 근거">',legacy_html,'</section>',
              '<p class="no-js-note">버블·색상은 관심도만 표현합니다. 당일 누적 qry_tp=4와 혼합하지 않습니다. 전체 표·회차·UUID·시세 근거는 v3 표 보기에서 확인하세요.</p></main></body></html>']
    return ''.join(parts)


def export(path, day, environment, output, test_mode=0):
    target,source=Path(output).absolute(),Path(path).resolve()
    if target.exists() or target.is_symlink():
        raise ValueError('기존 출력 파일은 덮어쓰지 않습니다.')
    if target.resolve()==source or target.suffix.lower() not in ('.html','.htm'):
        raise ValueError('DB와 다른 새 .html 경로를 지정하세요.')
    data=radar_v3.report(source,day,environment,test_mode)
    with target.open('x',encoding='utf-8') as handle:
        handle.write(render(data))
    return dict(output=str(target),date=day,environment=environment,qry_tp='1',test_mode=test_mode,
                collections=len(data['snapshots']),bubbles=len(model(data)['selected']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/stock1.db')
    parser.add_argument('--date',default=datetime.now(KST).date().isoformat())
    parser.add_argument('--environment',choices=('mock','real'),default='mock')
    parser.add_argument('--test-mode',choices=(0,1),type=int,default=0)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try: result=export(args.db,args.date,args.environment,args.output,args.test_mode)
    except (ValueError,OSError,sqlite3.Error) as error: parser.exit(1,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
