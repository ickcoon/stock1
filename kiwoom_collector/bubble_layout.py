"""Responsive display-only extension; existing analysis and renderers are preserved."""
import argparse
from datetime import datetime
import json
import math
import re
from pathlib import Path
import sqlite3
import unicodedata

from .client import KST
from .radar_v2 import e, interest_key
from . import bubble_timeline as timeline
from . import sector_bubble as bubble


MAIN_LIMIT = 3
MIN_HIT_RADIUS = 36


def capacity_for(count):
    return next(n for n in (1,2,3,4,6,8) if n>=max(1,count))


def optimize(source):
    """Copy presentation dictionaries only; never change analysis, membership or order."""
    result=dict(source,frames=[dict(f,model=dict(f['model'])) for f in source['frames']])
    for f in result['frames']:
        ranked=sorted([g for g in f['model']['groups'] if g['count']>0],key=lambda g:interest_key(g['current']))
        top=ranked[:MAIN_LIMIT];overlap=[]
        for i,a in enumerate(top):
            codes={s['stk_cd'] for s in a['current']['stocks']}
            for b in top[i+1:]:
                shared=codes & {s['stk_cd'] for s in b['current']['stocks']}
                if shared:overlap.append(dict(sectors=[a['name'],b['name']],shared_codes=sorted(shared)))
        f['focus']=dict(top=top,others=ranked[MAIN_LIMIT:],overlap=overlap)
    for mobile,key in ((False,'desktop'),(True,'mobile')):
        capacity=capacity_for(max((len(f['focus']['top']) for f in result['frames']),default=0))
        columns=min(2,capacity) if mobile else (capacity if capacity<=4 else 3 if capacity<=6 else 4)
        rows=math.ceil(capacity/columns)
        width=400 if mobile else (320 if capacity==1 else 600 if capacity==2 else 960)
        height=(220 if rows==1 else 340) if mobile else 240
        cell_w,cell_h=width/columns,height/rows
        def center(slot):
            return (width/2 if mobile and capacity==3 and slot==2 else cell_w*(slot%columns)+cell_w/2,
                    cell_h*(slot//columns)+cell_h/2)
        maximum=max((g['count'] for f in result['frames'] for g in f['focus']['top']),default=1)
        scale=(min(cell_w,cell_h)/2-18)/math.sqrt(maximum)
        active,remembered={},{}
        for f in result['frames']:
            selected=f['focus']['top']
            names={g['name'] for g in selected}
            active={n:slot for n,slot in active.items() if n in names}
            occupied=set(active.values())
            for g in selected:
                name=g['name']
                if name in active:continue
                old=remembered.get(name)
                slot=old if old is not None and old not in occupied else min(
                    (i for i in range(capacity) if i not in occupied),
                    key=lambda i:(i if mobile else (center(i)[0]-width/2)**2+(center(i)[1]-height/2)**2,i))
                active[name]=remembered[name]=slot;occupied.add(slot)
            f['model'][key]=dict(width=width,height=height,scale=scale,capacity=capacity,
                nodes=[dict(name=g['name'],id=g['id'],count=g['count'],slot=active[g['name']],rank=rank,
                    x=center(active[g['name']])[0],y=center(active[g['name']])[1],
                    r=scale*math.sqrt(g['count']),hit_r=max(MIN_HIT_RADIUS,scale*math.sqrt(g['count']))) for rank,g in enumerate(selected,1)])
    return result


def short_name(name,budget):
    """Explicit abbreviation, not clipping; full names remain in labels and panels."""
    units=lambda char:2 if unicodedata.east_asian_width(char) in ('W','F') else 1
    if sum(map(units,name))<=budget:return name
    used=0;chars=[]
    for char in name:
        if used+units(char)>budget-2:break
        chars.append(char);used+=units(char)
    return ''.join(chars)+'…'


def svg_map(geometry,groups,mobile=False):
    parts=[f'<svg class="bubble-svg {"mobile-map" if mobile else "desktop-map"}" viewBox="0 0 {geometry["width"]} {geometry["height"]}" role="group" aria-label="반응형 섹터 관심 지도">']
    for node in geometry['nodes']:
        g=groups[node['name']];x,y,r,hit=node['x'],node['y'],node['r'],node['hit_r']
        compact=r<38
        name=short_name(g['name'],10 if compact else max(10,int((2*r-16)/7)*2))
        summary=f'{g["name"]} · TOP20 {g["count"]} · 직전 {bubble.delta_label(g["delta20"])}'
        rank=node['rank']
        parts += [f'<g class="bubble-node node-{g["id"]} {"priority-primary" if rank<=2 else "priority-support"}" data-sector="{e(g["name"])}" data-count="{g["count"]}" data-state="{g["state"]}" data-rank="{rank}"><title>{e(summary)}</title>',
            f'<circle class="bubble-circle {g["state"]}" cx="{x}" cy="{y}" r="{r:.8f}" fill="{bubble.COLORS[g["state"]]}"/>',
            f'<foreignObject x="{x-hit:.8f}" y="{y-hit:.8f}" width="{2*hit:.8f}" height="{2*hit:.8f}"><label xmlns="http://www.w3.org/1999/xhtml" class="bubble-label optimized-label {"compact-mark" if compact else ""} {"medium-mark" if r<65 else ""}" for="pick-{g["id"]}" title="{e(summary)}" style="--r:{r}px">',
            f'<span class="name">{e(name)}</span><span class="count">{g["count"]}</span>',
            f'<span class="change">{e(bubble.delta_label(g["delta20"]))}</span>' if not compact else '']
        if r>=75 and g['current'] and g['current']['stocks']:
            parts.append('<span class="representative">'+e(g['current']['stocks'][0]['stk_nm'])+'</span>')
        parts.append('</label></foreignObject>')
        badge=f'TOP{rank} · '+('우선' if rank<=2 else '보조')+(' · 신규' if g['new_sector'] else '')
        parts.append(f'<text class="rank-mark {"primary-mark" if rank<=2 else "support-mark"} {"is-new" if g["new_sector"] else ""}" x="{x}" y="{y-r-6:.8f}" text-anchor="middle">{badge}</text>')
        parts.append('</g>')
    return ''.join(parts)+'</svg>'


CSS='''
.bubble-root .desktop-map{max-height:410px}.bubble-root .mobile-map{max-height:none}
.bubble-root .bubble-svg foreignObject{overflow:visible}
.optimized-label{padding:5px;position:relative}.optimized-label .name{font-size:13px;line-height:1.2}
.optimized-label .count{font-size:27px;line-height:1.2;margin:2px 0}.optimized-label .change{font-size:11px}
.optimized-label.medium-mark .name{font-size:11px}.optimized-label.medium-mark .count{font-size:19px}.optimized-label.medium-mark .change{font-size:10px}
.optimized-label.compact-mark{overflow:visible}
.optimized-label.compact-mark .name{position:absolute;top:calc(50% + var(--r) + 3px);color:#26394d;background:#fff;border-radius:3px;padding:1px 3px;white-space:nowrap;font-size:10px;max-width:none}
.optimized-label.compact-mark .count{font-size:16px;margin:0}.new-mark{fill:#a03236;font-size:10px;font-weight:700;pointer-events:none}
.reading-guide{margin:4px 0 7px;font-size:11px}.reading-guide summary{padding:3px 0}.reading-guide .meaning{font-size:12px}
.all-sectors{margin-top:8px}.all-sectors>summary{font-size:12px;font-weight:600;padding:5px 0}
.all-sectors .map-key{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px}
.all-sectors .sector-chip{display:inline-flex;align-items:center;min-height:44px;padding:7px 9px;font-size:12px;white-space:normal;overflow-wrap:anywhere;max-width:100%}
.all-sectors .outside{display:flex;flex-wrap:wrap;gap:4px}.all-sectors .outside b{flex-basis:100%}.all-sectors details.outside{display:block}
.map-explainer{font-size:10px;margin-top:4px}.detail-jump{display:none;align-items:center;min-height:44px;padding:7px 12px;border:1px solid #c9d8e7;border-radius:6px;color:#245aa7;text-decoration:none;font-size:12px;margin-top:7px}
.detail-card{scroll-margin-top:10px}.detail-card:target{outline:2px solid #779bc0;outline-offset:2px}
.rank-mark{font-size:10px;fill:#5d6e80;pointer-events:none}.primary-mark{font-weight:750;fill:#26394d;font-size:11px}.is-new{fill:#a03236}
.priority-primary .bubble-circle{stroke:#40546b;stroke-width:2.5}.priority-support .bubble-circle{stroke:#a1adba;stroke-width:1.2}
.focus-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin:8px 0 3px}.focus-card{display:block;cursor:pointer;border:1px solid #d4dfeb;background:#f6f9fc;border-radius:7px;padding:6px 9px;min-height:49px;font-size:12px;min-width:0;overflow-wrap:anywhere}
.focus-card b{font-size:13px}.focus-card small{font-size:10px;overflow-wrap:anywhere}.support-line{font-size:11px;color:#65778a;margin:3px 0}
.top-overlap{font-size:10px;margin:3px 0}.top-overlap>summary{color:#866018}.top-overlap p{overflow-wrap:anywhere}
.screen-count-mobile{display:none}.detail-scroll{max-height:310px}
@media(max-width:760px){.screen-count-mobile{display:inline}.screen-count-desktop{display:none}.bubble-root .mobile-map{display:block}.bubble-root .desktop-map{display:none}
.bubble-root .brief{display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;padding:7px 10px}
.bubble-root .brief>div{min-width:0;white-space:nowrap}.bubble-root .brief span{display:block;font-size:10px;line-height:1.1}
.bubble-root .brief b{display:block;font-size:14px;line-height:1.3}
.bubble-root .view-controls>.no-js-note{display:none}.bubble-root .legend{gap:5px 8px;font-size:10px;margin-top:6px}
.bubble-root .map-heading{gap:2px}.all-sectors .map-key{grid-template-columns:repeat(2,minmax(0,1fr))}
.bubble-root .sidebar{display:flex;flex-direction:column}.bubble-root .detail-card{order:0}.bubble-root .summary-card{order:1}
.optimized-label .name{font-size:14px}.optimized-label .count{font-size:28px}
.optimized-label.medium-mark .name{font-size:11px}.optimized-label.medium-mark .count{font-size:19px}
.optimized-label.compact-mark .name{font-size:10px}.optimized-label.compact-mark .count{font-size:16px}
.legacy-panel .scroll>table,.legacy-panel .timeline table{min-width:0;table-layout:fixed}
.legacy-panel th,.legacy-panel td{white-space:normal!important;overflow-wrap:anywhere;font-size:10px;padding:4px 2px}
.detail-jump{display:flex;justify-content:center}.detail-scroll{max-height:none}
}
'''


def frame_html(frame):
    data,m=frame['data'],frame['model']
    html=timeline.frame_html(frame)
    groups={g['name']:g for g in m['groups']}
    focus=frame['focus'];top=focus['top']
    if m['selected']:
        for mobile,key in ((False,'desktop'),(True,'mobile')):
            original=bubble.svg_map(m[key],groups,mobile)
            if html.count(original)!=1:raise ValueError('타임라인 SVG 구조를 확인하세요.')
            html=html.replace(original,svg_map(m[key],groups,mobile),1)
    html=html.replace('<h2>섹터 관심 지도</h2>','<h2>관심섹터 TOP3 · TOP2 우선</h2>',1)
    cards='<div class="focus-two">'+''.join(
        f'<label class="focus-card" for="pick-{g["id"]}"><b>TOP{i} · {e(g["name"])}</b><small>대표 · '+
        e(' · '.join(s['stk_nm'] or s['stk_cd'] for s in g['current']['stocks'][:2]))+'</small></label>'
        for i,g in enumerate(top[:2],1))+'</div>'
    if top:html=html.replace('<div class="map-frame">',cards+'<div class="map-frame">',1)
    # Keep all existing selectors, radio inputs, sector details and v3 diagnostics.
    start=html.index('<p class="meaning">');end=html.index('</p>',start)+4
    html=html[:start]+'<details class="reading-guide"><summary>읽는 법 · 색상은 주가 등락률이 아닙니다</summary>'+html[start:end]+'</details>'+html[end:]
    start=html.index('<div class="map-key">');end=html.index('<p class="map-foot">',start)
    total=len(m['selected'])+len(m['remainder'])
    desktop=len(m['desktop']['nodes']);mobile=len(m['mobile']['nodes'])
    summary=f'전체 {total}섹터 목록 · TOP3 밖 {max(0,total-desktop)}'
    if m['exits']:summary+=f' · 현재 이탈 {len(m["exits"])}'
    support=''
    if len(top)==3:
        g=top[2];representative=g['current']['stocks'][0]
        support=f'<p class="support-line">TOP3 보조 · {e(g["name"])} · 대표 {e(representative["stk_nm"] or representative["stk_cd"])}</p>'
    overlap=''
    if focus['overlap']:
        stock_names={s['stk_cd']:s['stk_nm'] or s['stk_cd'] for s in data['current']['stocks']}
        overlap=f'<details class="top-overlap"><summary>TOP3 종목 중복 {len(focus["overlap"])}쌍 · 태그별 집계</summary>'+''.join(
            '<p>'+e(' ↔ '.join(pair['sectors']))+' · '+e(' / '.join(stock_names[c]+' ('+c+')' for c in pair['shared_codes']))+'</p>' for pair in focus['overlap'])+'<p>동일 종목의 복수 소속입니다. 섹터를 자동 통합하거나 순위에서 제외하지 않습니다.</p></details>'
    html=html[:start]+support+overlap+f'<details class="all-sectors"><summary>{summary}</summary>'+html[start:end]+'</details><a class="detail-jump" href="about:srcdoc#selected-detail">선택 섹터 상세로 이동 ↓</a>'+html[end:]
    start=html.index('<p class="map-foot">');end=html.index('</p>',start)+4
    html=html[:start]+'<details class="map-explainer"><summary>면적·배치 기준</summary>'+html[start:end]+'</details>'+html[end:]
    html=html.replace('<section class="detail-card">','<section class="detail-card" id="selected-detail" tabindex="-1">',1)
    original=f'현재 {len(m["selected"])} / {total}섹터 표시 · 크기에 지속성 가중 없음'
    replacement=f'관심도순 {desktop}/{total}섹터 · 나머지는 접힌 목록에서 선택'
    html=html.replace(original,replacement,1)
    if top:
        def initial_choice(match):
            tag=re.sub(r'\schecked(?=\s|$)','',match.group(1)).rstrip()
            return tag+(' checked' if f'id="pick-{top[0]["id"]}"' in tag else '')+'>'
        html=re.sub(r'(<input class="sr-only sector-choice"[^>]*)(>)',initial_choice,html)
    return html.replace('</head>','<style>'+CSS+'</style></head>',1)


def report(path,day,environment='mock'):
    return optimize(timeline.report(path,day,environment))


def render(data):
    html=timeline.render(data,frame_renderer=frame_html)
    text='선택 시점까지의 성공 관측만 계산 · 미관측/실패/누적순위는 선택 항목에서 제외 · 현재 태그 기준'
    html=html.replace('<span>'+text+'</span>','<span class="timeline-note-desktop">'+text+'</span><details class="timeline-note-mobile"><summary>회차 기준</summary><p>'+text+'</p></details>',1)
    outer='''<style>.timeline-note-mobile{display:none}.timeline-note-mobile p{max-width:280px}
    @media(max-width:760px){.timeline-note-desktop{display:none}.timeline-note-mobile{display:block}.timeline-note-mobile summary{cursor:pointer}}</style>'''
    return html.replace('</head>',outer+'</head>',1)


def export(path,day,environment,output):
    target,source=Path(output).absolute(),Path(path).resolve()
    if target.exists() or target.is_symlink():raise ValueError('기존 출력 파일은 덮어쓰지 않습니다.')
    if target.resolve()==source or target.suffix.lower() not in ('.html','.htm'):raise ValueError('DB와 다른 새 .html 경로를 지정하세요.')
    data=report(source,day,environment)
    with target.open('x',encoding='utf-8') as handle:handle.write(render(data))
    return dict(output=str(target),date=day,environment=environment,qry_tp='1',collections=len(data['frames']),default_index=data['default_index'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/stock1.db');parser.add_argument('--date',default=datetime.now(KST).date().isoformat())
    parser.add_argument('--environment',choices=('mock','real'),default='mock');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:result=export(args.db,args.date,args.environment,args.output)
    except (ValueError,OSError,sqlite3.Error) as error:parser.exit(1,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
