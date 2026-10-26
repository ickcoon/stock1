"""Read-only intraday timeline; preserves Bubble v1 and every v3 disclosure.

python -m kiwoom_collector.bubble_timeline --date YYYY-MM-DD --output new.html
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import tempfile

from .client import KST
from .radar import radar_report, timestamp
from .radar_v2 import e
from . import radar_v3, sector_bubble as bubble
from .summary import read_database


def position_key(run):
    return timestamp(run['collected_at']), run['sequence']


def build(legacy, attempts=(), *, as_of=None):
    """Pure frame builder. Only operating qry_tp=1; exact rowid prefixes.

    Unlike a timestamp-only cutoff, a rowid prefix distinguishes observations
    collected at the same instant. Future ranks/quotes never enter a prefix.
    """
    as_of = as_of or datetime.now(KST)
    latest = radar_v3.analyze(legacy, attempts, test_mode=0, as_of=as_of)
    runs = latest['legacy']['runs']
    frames = []
    for i, run in enumerate(runs):
        cut = position_key(run)
        prefix = dict(latest['legacy'], runs=runs[:i+1])
        observed_attempts = [a for a in attempts if timestamp(a.get('collected_at')) is not None
                             and position_key(a) <= cut]
        data = radar_v3.analyze(prefix, observed_attempts, test_mode=0, as_of=cut[0])
        # Analysis horizon is the selected observation; generation is the real export time.
        data['generated_at'] = as_of.isoformat()
        frames.append(dict(index=i, collection_id=run['id'], collected_at=run['collected_at'],
                           sequence=run['sequence'], data=data, model=bubble.model(data)))
    stabilize(frames)
    return dict(date=legacy['date'], environment=legacy['environment'], qry_tp='1', test_mode=0,
                generated_at=as_of.isoformat(), frames=frames, latest=latest,
                default_index=len(frames)-1 if frames else None)


def stabilize(frames):
    """Keep persistent sectors in their cells, reclaim only vacant cells.

    One scale for the entire day's frames makes equal values equal areas even
    across time. Eight cells also reserve absent-sector space on mobile.
    """
    maximum = max((g['count'] for f in frames for g in f['model']['selected']), default=1)
    active, remembered = {}, {}
    for frame in frames:
        m = frame['model']
        names = {g['name'] for g in m['selected']}
        active = {name:slot for name,slot in active.items() if name in names}
        occupied = set(active.values())
        for name in sorted(names-active.keys(), key=bubble.identity):
            home = remembered.get(name, int(bubble.identity(name)[1:9],16) % bubble.MAX_BUBBLES)
            slot = next((home+i) % bubble.MAX_BUBBLES for i in range(bubble.MAX_BUBBLES)
                        if (home+i) % bubble.MAX_BUBBLES not in occupied)
            active[name] = remembered[name] = slot
            occupied.add(slot)
        for mobile, key in ((False,'desktop'),(True,'mobile')):
            cell,columns = (220,2) if mobile else (240,4)
            scale = min(62,(100 if mobile else 102)/math.sqrt(maximum))
            m[key] = dict(width=cell*columns,height=cell*(bubble.MAX_BUBBLES//columns),scale=scale,
                          nodes=[dict(name=g['name'],id=g['id'],slot=active[g['name']],count=g['count'],
                                      x=cell*(active[g['name']] % columns)+cell/2,
                                      y=cell*(active[g['name']]//columns)+cell/2,
                                      r=scale*math.sqrt(g['count'])) for g in m['selected']])


def report(path, day, environment='mock'):
    # One source transaction supplies every observation and attempt, including WAL.
    with tempfile.TemporaryDirectory(prefix='stock1-bubble-timeline-') as folder:
        snapshot = Path(folder)/'snapshot.db'
        with read_database(path) as (source,resolved):
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        legacy = radar_report(snapshot,day,environment)
        legacy['db'] = str(resolved)
        with read_database(snapshot) as (db,_):
            attempts = [dict(r) for r in db.execute('SELECT rowid AS sequence,* FROM collections')]
        return build(legacy,attempts)


def frame_html(frame):
    """Reuse v1 verbatim, substituting only its two deterministic SVG geometries."""
    data,m = frame['data'],frame['model']
    original = bubble.model(data)
    html = bubble.render(data)
    groups = {g['name']:g for g in m['groups']}
    for mobile,key in ((False,'desktop'),(True,'mobile')):
        old = bubble.svg_map(original[key],groups,mobile)
        replacement = bubble.svg_map(m[key],groups,mobile)
        if m['selected']:
            if html.count(old) != 1:
                raise ValueError('Bubble v1 SVG 구조가 변경되었습니다. 타임라인 호환성을 확인하세요.')
            html = html.replace(old,replacement,1)
    # Preserve Bubble v1's document, CSP, controls and complete v3 body.
    additions = '''<style>
    .bubble-root{padding:0 14px 12px}.bubble-head{display:none}
    .desktop-map{max-height:340px}.brief{margin-top:0}
    .map-foot{font-size:10px}.detail-scroll{max-height:300px}
    .bubble-label .name{font-size:13px}.bubble-label .count{font-size:24px}
    .bubble-label .change{font-size:11px}
    @media(max-width:540px){.bubble-root{padding:0 10px 12px}}
    </style>'''
    return html.replace('</head>',additions+'</head>',1)


STYLE = '''
:root{color-scheme:light;--ink:#26394d;--muted:#65778a;--line:#dce5ed}
*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:var(--ink);font:13px/1.4 system-ui,"Malgun Gothic",sans-serif}
.timeline-shell{max-width:1440px;margin:auto}.shell-header{padding:12px 18px 4px;display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
h1{font-size:20px;margin:0;letter-spacing:-.4px}.muted{color:var(--muted)}.badge{display:inline-block;border:1px solid var(--line);border-radius:5px;background:white;padding:3px 8px;font-size:11px}
.timeline-controls{margin:3px 18px 6px;border:1px solid var(--line);border-radius:9px;background:white;padding:7px 10px;min-width:0}
.timeline-controls legend{font-size:11px;color:var(--muted);padding:0 4px}.time-list{display:flex;gap:5px;overflow-x:auto;padding:2px 1px 4px;align-items:center}
.time-label{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;padding:5px 9px;border:1px solid var(--line);border-radius:6px;cursor:pointer;white-space:nowrap;font-size:12px;min-height:30px}
.time-label small{font-size:9px}.time-label:hover{background:#eef3f8}.sr-only{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
.control-foot{display:flex;gap:10px;align-items:center;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:3px;flex-wrap:wrap}
.motion-toggle{white-space:nowrap;cursor:pointer}.frame{display:none;width:100%;height:calc(100dvh - 153px);min-height:640px;border:0;background:#f2f5f8}
.frame.latest{display:block}.empty{margin:12px 18px;padding:18px;border:1px solid var(--line);border-radius:10px;background:white}
@supports selector(:has(*)){.frame.latest{display:none}}
@keyframes frame-in{from{opacity:.6}to{opacity:1}}
.timeline-shell:has(#motion-off:checked) .frame{animation:none!important}
@media(prefers-reduced-motion:reduce){.frame{animation:none!important}}
@media(max-width:600px){.shell-header{padding:10px 10px 3px;gap:4px}h1{font-size:18px}.timeline-controls{margin:3px 10px 6px}.time-label{padding:5px 8px}.time-list{flex-wrap:wrap}.frame{height:calc(100dvh - 260px);min-height:480px}}
@supports not selector(:has(*)){.time-list,.motion-toggle{display:none}}
'''


def render(timeline, *, frame_renderer=None):
    frame_renderer = frame_renderer or frame_html
    frames = timeline['frames']
    duplicate_times = Counter(timestamp(f['collected_at']) for f in frames)
    ordinal = Counter()
    rules = ''.join(f'.timeline-shell:has(#time-{f["index"]}:checked) #frame-{f["index"]}'+'{display:block;animation:frame-in 120ms ease-out}'+
                    f'.timeline-shell:has(#time-{f["index"]}:checked) label[for=time-{f["index"]}]'+'{background:#26394d;color:white;border-color:#26394d}'+
                    f'#time-{f["index"]}:focus-visible+label'+'{outline:3px solid #e5aa37;outline-offset:2px}' for f in frames)
    parts = ['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; frame-src \'self\' about:; base-uri \'none\'; form-action \'none\'">',
             '<title>STOCK1 · Bubble Radar 장중 타임라인</title><style>'+STYLE+rules+'</style></head><body><main class="timeline-shell">',
             '<header class="shell-header"><h1>Sector Bubble Radar <span class="muted">· 장중 타임라인</span></h1>',
             f'<div><span class="badge">{e(timeline["date"])}</span> <span class="badge">{e(timeline["environment"])} · {"모의" if timeline["environment"]=="mock" else "실전"}</span> <span class="badge">qry_tp=1 · 운영</span></div></header>']
    if not frames:
        return ''.join(parts)+'<section class="empty">선택 날짜·환경·qry_tp=1에 성공한 운영 관측이 없습니다. 테스트·누적순위는 타임라인에 포함하지 않습니다.</section></main></body></html>'
    parts += [f'<fieldset class="timeline-controls"><legend>실제 성공 관측 {len(frames)}회 · KST 시간 선택</legend><div class="time-list" role="group" aria-label="관측 회차 선택">']
    for frame in frames:
        index,at=frame['index'],timestamp(frame['collected_at'])
        ordinal[at]+=1
        label = at.strftime('%H:%M:%S')
        tie = f' · 같은 시각 {ordinal[at]}/{duplicate_times[at]}' if duplicate_times[at]>1 else ''
        parts.append(f'<input class="sr-only" type="radio" name="observation" id="time-{index}" aria-label="{label}{tie} 회차 선택" {"checked" if index==timeline["default_index"] else ""}><label class="time-label" for="time-{index}">{label}'+(f'<small>같은 시각 {ordinal[at]}/{duplicate_times[at]}</small>' if tie else '')+'</label>')
    parts += ['</div><div class="control-foot"><span>선택 시점까지의 성공 관측만 계산 · 미관측/실패/누적순위는 선택 항목에서 제외 · 현재 태그 기준</span>',
              '<label class="motion-toggle"><input type="checkbox" id="motion-off"> 전환 효과 끄기</label></div></fieldset>']
    for f in frames:
        label=timestamp(f['collected_at']).strftime('%H:%M:%S')
        parts.append(f'<iframe class="frame {"latest" if f["index"]==timeline["default_index"] else ""}" id="frame-{f["index"]}" data-collection="{e(f["collection_id"])}" title="{e(timeline["date"])} {label} 관측 {f["index"]+1} 상세" sandbox="allow-same-origin" srcdoc="{e(frame_renderer(f))}"></iframe>')
    return ''.join(parts)+'</main></body></html>'


def export(path,day,environment,output):
    target,source=Path(output).absolute(),Path(path).resolve()
    if target.exists() or target.is_symlink():
        raise ValueError('기존 출력 파일은 덮어쓰지 않습니다.')
    if target.resolve()==source or target.suffix.lower() not in ('.html','.htm'):
        raise ValueError('DB와 다른 새 .html 경로를 지정하세요.')
    timeline=report(source,day,environment)
    with target.open('x',encoding='utf-8') as handle:handle.write(render(timeline))
    return dict(output=str(target),date=day,environment=environment,qry_tp='1',test_mode=0,
                collections=len(timeline['frames']),default_index=timeline['default_index'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/stock1.db')
    parser.add_argument('--date',default=datetime.now(KST).date().isoformat())
    parser.add_argument('--environment',choices=('mock','real'),default='mock')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:result=export(args.db,args.date,args.environment,args.output)
    except (ValueError,OSError,sqlite3.Error) as error:parser.exit(1,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
