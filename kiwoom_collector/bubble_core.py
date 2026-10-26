"""Read-only TOP5 observation panels on the preserved TOP3 Bubble timeline."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from . import bubble_layout as layout
from . import sector_bubble as bubble
from .client import KST
from .quotes import CODE
from .radar import timestamp
from .radar_v2 import e, money, rate_label
from .radar_v3 import INTRADAY_SLOTS


NOTICE = '조회 관심도 기반 핵심 관찰종목 — 매매 후보 미확정'


def unique_stocks(snapshot):
    """One valid code per observation; keep its best observed query rank."""
    stocks = {}
    for stock in snapshot['stocks']:
        code, rank = stock.get('stk_cd'), stock.get('rank')
        if (isinstance(code, str) and CODE.fullmatch(code) and type(rank) is int
                and 1 <= rank <= 20 and (code not in stocks or rank < stocks[code]['rank'])):
            stocks[code] = stock
    return stocks


def complete(stocks, limit):
    return {s['rank'] for s in stocks.values() if s['rank'] <= limit} == set(range(1, limit+1))


def available(quote, cutoff):
    if not quote:
        return False
    times=[timestamp(quote.get(key)) for key in ('request_finished_at','api_at')]
    return all(at is not None and at<=cutoff for at in times)


def connection(previous, current):
    """Only adjacent, explicitly recorded regular slots prove continuity."""
    a, b = previous['collection'], current['collection']
    pa, pb = timestamp(a.get('scheduled_at')), timestamp(b.get('scheduled_at'))
    if pa is None or pb is None:
        return False, '예정 회차 연결 미확인'
    if (pa.date() != pb.date() or pa.strftime('%H:%M') not in INTRADAY_SLOTS
            or pb.strftime('%H:%M') not in INTRADAY_SLOTS
            or pa.second or pb.second or pa.microsecond or pb.microsecond):
        return False, '예정 회차 연결 미확인'
    if INTRADAY_SLOTS.index(pb.strftime('%H:%M')) != INTRADAY_SLOTS.index(pa.strftime('%H:%M'))+1:
        return False, '예정 회차 누락 또는 비연속'
    ca, cb = timestamp(a['collected_at']), timestamp(b['collected_at'])
    if ca < pa or cb < pb or ca >= pb:
        return False, '지연 관측으로 예정 구간 연결 미확인'
    return True, '인접 예정 회차 확인'


def stock_metrics(data):
    """Global per-code prefix metrics; sector membership never multiplies counts."""
    if not data['current']:
        return dict(stocks={}, observations=0, continuity='첫 관측', incomplete=False)
    selected = data['current']['collection']
    cutoff = timestamp(selected['collected_at']), selected['sequence']
    snapshots = []
    for snapshot in data['snapshots']:
        row = snapshot['collection']; at = timestamp(row.get('collected_at'))
        if (at is not None and at.date().isoformat() == data['date']
                and row.get('environment') == data['environment'] and row.get('qry_tp') == '1'
                and row.get('api_id') == 'ka00198' and row.get('status') == 'success'
                and row.get('test_mode') == 0 and (at, row['sequence']) <= cutoff):
            snapshots.append(snapshot)
    snapshots.sort(key=lambda s: (timestamp(s['collection']['collected_at']), s['collection']['sequence']))
    maps = [unique_stocks(s) for s in snapshots]
    if not maps:
        return dict(stocks={}, observations=0, continuity='비교 불가', incomplete=False)
    now, old = maps[-1], maps[-2] if len(maps)>1 else None
    links = [connection(a,b) for a,b in zip(snapshots,snapshots[1:])]
    result = {}
    for code in set().union(*(m.keys() for m in maps)):
        appearances = [i for i,m in enumerate(maps) if code in m]
        stock = maps[appearances[-1]][code]
        current, previous = now.get(code), old.get(code) if old is not None else None
        rank = current['rank'] if current else None
        before = previous['rank'] if previous else None
        in5, was5 = rank is not None and rank<=5, before is not None and before<=5
        if old is None:
            state = '비교 불가'
        elif in5:
            state = '유지' if was5 else '신규 진입' if previous or complete(old,5) else '비교 불가'
        elif was5:
            state = '이탈' if current or complete(now,5) else '비교 불가'
        else:
            state = 'TOP5 밖' if current or complete(now,5) else '비교 불가'
        if old is None:
            top20_state = '비교 불가'
        elif current:
            top20_state = '유지' if previous else '신규 진입' if complete(old,20) else '비교 불가'
        else:
            top20_state = '이탈' if previous and complete(now,20) else '관측 미확인' if not complete(now,20) else '현재 없음'
        streak = 0
        for i in range(len(maps)-1,-1,-1):
            if code not in maps[i] or maps[i][code]['rank']>5:
                break
            streak += 1
            if i and not links[i-1][0]:
                break
        sectors = {t['sector_name'] for i in appearances for t in maps[i][code]['tags']}
        rate_ok=bool(current and available(current.get('valid_quote'),cutoff[0]))
        cash_ok=bool(current and current.get('cash') is not None and available(current.get('cash_quote'),cutoff[0]))
        later_quote=bool(current and any(q and not available(q,cutoff[0])
                         for q in (current.get('valid_quote'),current.get('cash_quote'))))
        result[code] = dict(code=code,name=stock['stk_nm'] or code,rank=rank,previous_rank=before,
            top5_appearances=sum(code in m and m[code]['rank']<=5 for m in maps),
            top5_streak=streak,top20_appearances=len(appearances),
            best_rank=min(maps[i][code]['rank'] for i in appearances),
            top5_state=state,top20_state=top20_state,sectors=sorted(sectors),
            rate_verified=rate_ok,cash_verified=cash_ok,
            rate=rate_label(current) if rate_ok else '선택 시각까지 검증된 시세 없음',
            cash=money(current.get('cash')) if cash_ok else '미확인',
            cash_reason=('선택 시각 이후 또는 시각 미확인 시세는 패널에서 제외' if later_quote else
                         current.get('cash_reason') if current else '현재 회차 관측 없음'),
            quote_cutoff=selected['collected_at'],
            liquidity_verified=False,candidate_confirmed=False)
    return dict(stocks=result,observations=len(maps),
        continuity=links[-1][1] if links else '첫 관측 · 비교 불가',
        incomplete=any(not complete(m,20) for m in maps))


def enhance(source):
    result = dict(source,frames=[dict(f) for f in source['frames']])
    for frame in result['frames']:
        metrics = stock_metrics(frame['data'])
        sectors = {}
        for g in frame['model']['groups']:
            members = sorted([s for s in metrics['stocks'].values() if g['name'] in s['sectors']],
                             key=lambda s:(s['rank'] is None,s['rank'] or s['previous_rank'] or 99,s['code']))
            sectors[g['id']] = dict(
                core=[s for s in members if s['rank'] is not None and s['rank']<=5],
                support=[s for s in members if s['rank'] is not None and s['rank']>5],
                exits=[s for s in members if s['top5_state']=='이탈'],
                absent=[s for s in members if s['rank'] is None and s['top5_state']!='이탈'])
        frame['core'] = dict(metrics=metrics,sectors=sectors)
    return result


def card(stock, environment, observations):
    s=stock
    mode='모의' if environment=='mock' else '실전'
    current=str(s['rank'])+'위' if s['rank'] is not None else '관측 없음'
    previous=str(s['previous_rank'])+'위' if s['previous_rank'] is not None else '—'
    return (f'<li class="core-stock" data-code="{s["code"]}" data-top5-state="{e(s["top5_state"])}">'
        f'<div class="core-stock-head"><b>{e(s["name"])}</b><strong>{current}</strong></div>'
        f'<div class="core-state">TOP5 {e(s["top5_state"])} · TOP20 {e(s["top20_state"])} · 직전 {previous}</div>'
        '<div class="core-numbers">'
        f'<span>TOP5 <b>{s["top5_appearances"]}/{observations}회</b></span>'
        f'<span>연속 <b>{s["top5_streak"]}회</b></span><span>당일 최고 <b>{s["best_rank"]}위</b></span></div>'
        f'<div class="core-validation">시세 {"검증 통과("+mode+")" if s["rate_verified"] else "미확인"} · '
        f'거래대금 {"검증 통과("+mode+")" if s["cash_verified"] else "미검증"} · 유동성 미검증</div>'
        '<details class="core-evidence"><summary>종목 근거</summary>'
        f'<p>{s["code"]} · TOP20 {s["top20_appearances"]}/{observations}회 · {e(s["rate"])}<br>'
        f'거래대금 {e(s["cash"])} · {e(s["cash_reason"] or "검증된 관측 금액")}<br>'
        '조회순위 기준 · 거래대금 순위·대장주 확정 아님 · 매매 후보 미확정</p></details></li>')


def panel(group, frame):
    data=frame['data']; core=frame['core']; metrics=core['metrics']; sector=core['sectors'][group['id']]
    original=bubble.panel(group,data)
    end=original.index('</h2>')+5
    rows=lambda stocks: '<ul class="core-stocks">'+''.join(card(s,data['environment'],metrics['observations']) for s in stocks)+'</ul>'
    parts=[original[:end],'<div class="core-observation"><h3>TOP5 핵심 관찰종목</h3>',
        '<p class="core-notice">'+NOTICE+'</p>',
        '<p class="core-scope">선택 시각까지 '+str(metrics['observations'])+'회 · '+e(metrics['continuity'])+'</p>']
    if metrics['incomplete']:
        parts.append('<p class="core-scope">불완전 표본 포함 · 등장 횟수는 확인된 관측만, 누락 종목의 이탈은 추정하지 않음</p>')
    parts.append(rows(sector['core']) if sector['core'] else '<p class="core-empty">현재 이 섹터의 TOP5 관찰종목 없음</p>')
    for key,title in (('exits','TOP5 이탈'),('support','보조 관찰 · 나머지 TOP20'),('absent','과거 관찰 · 현재 없음/미확인')):
        stocks=sector[key]
        if stocks:
            repeated=sum(s['top20_appearances']>=2 for s in stocks)
            parts.append(f'<details class="core-list {key}"><summary>{title} {len(stocks)}종목'+
                         (f' · TOP20 반복 {repeated}' if key=='support' else '')+'</summary>'+rows(stocks)+'</details>')
    parts.append('<details class="core-rules"><summary>TOP5 집계·연속 기준</summary><p>개별 코드별 성공 회차를 한 번씩 집계합니다. 연속 횟수는 인접한 예정 회차가 확인된 경우만 이어집니다. 누락·불명·비연속 회차는 끊으며 직전 성공 회차 대비 유지/이탈과 구분합니다. 당일 최고는 선택 시각까지의 관측 최고입니다. 기존 섹터 관측 연속 수와 계산 기준이 다릅니다.</p></details></div>')
    parts.append('<details class="original-sector-detail"><summary>기존 섹터 상세·검증 근거</summary><p class="core-scope">기존 회차 진단 원본입니다. 순위 이후 완료된 동일 회차 시세도 포함할 수 있으며 위 관찰 패널의 선택 시각 기준 검증과 구분합니다.</p>'+original[end:-10]+'</details></article>')
    return ''.join(parts)


CSS='''
.core-observation h3{font-size:14px;margin:8px 0 4px}.core-notice{font-size:11px;line-height:1.4;color:#8a5726;background:#fff7e9;padding:6px 8px;border-radius:5px;margin:5px 0}
.core-scope,.core-empty{font-size:11px;color:#65778a;margin:4px 0}.core-stocks{list-style:none;padding:0;margin:5px 0;display:grid;gap:6px}
.core-stock{border:1px solid #dce5ed;border-radius:7px;padding:7px 8px;min-width:0;overflow-wrap:anywhere;background:#fafcfe}
.core-stock-head{display:flex;align-items:baseline;justify-content:space-between;gap:6px;font-size:13px}.core-stock-head b{min-width:0}.core-stock-head strong{white-space:nowrap;color:#245aa0;font-size:15px}
.core-state{font-size:10px;color:#65778a;margin:2px 0 4px}.core-numbers{display:flex;gap:5px 11px;flex-wrap:wrap;font-size:10px}.core-numbers b{font-size:12px}
.core-validation{font-size:10px;color:#65778a;margin-top:4px}.core-evidence{font-size:10px;margin-top:3px}.core-evidence p{margin:3px 0}
.core-list>summary,.core-rules>summary,.original-sector-detail>summary{font-size:11px;font-weight:600;min-height:36px;display:list-item;align-content:center;cursor:pointer}
.core-list,.core-rules,.original-sector-detail{border-top:1px solid #e5ebf0;margin-top:5px}.core-rules p{font-size:11px}.original-sector-detail{font-size:11px}
.detail-scroll{max-height:435px}.detail-intro{font-size:12px!important;margin-bottom:3px!important}
@media(max-width:760px){.detail-scroll{max-height:none}.core-list>summary,.core-rules>summary,.original-sector-detail>summary,.core-evidence>summary{min-height:44px;align-content:center}}
'''


def frame_html(frame):
    html=layout.frame_html(frame)
    for group in frame['model']['groups']:
        original=bubble.panel(group,frame['data'])
        if html.count(original)!=1:
            raise ValueError('기존 섹터 상세 구조를 확인하세요.')
        html=html.replace(original,panel(group,frame),1)
    return html.replace('</head>','<style>'+CSS+'</style></head>',1)


def render(data):
    html=layout.render(data)
    for frame in data['frames']:
        old=e(layout.frame_html(frame))
        if html.count(old)!=1:
            raise ValueError('기존 타임라인 프레임 구조를 확인하세요.')
        html=html.replace(old,e(frame_html(frame)),1)
    return html


def report(path, day, environment='mock'):
    return enhance(layout.report(path,day,environment))


def export(path, day, environment, output):
    target,source=Path(output).absolute(),Path(path).resolve()
    if target.exists() or target.is_symlink():
        raise ValueError('기존 출력 파일은 덮어쓰지 않습니다.')
    if target.resolve()==source or target.suffix.lower() not in ('.html','.htm'):
        raise ValueError('DB와 다른 새 .html 경로를 지정하세요.')
    data=report(source,day,environment)
    with target.open('x',encoding='utf-8') as handle:
        handle.write(render(data))
    return dict(output=str(target),date=day,environment=environment,qry_tp='1',test_mode=0,
                collections=len(data['frames']),default_index=data['default_index'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/stock1.db')
    parser.add_argument('--date',default=datetime.now(KST).date().isoformat())
    parser.add_argument('--environment',choices=('mock','real'),default='mock')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        result=export(args.db,args.date,args.environment,args.output)
    except (ValueError,OSError,sqlite3.Error) as error:
        parser.exit(1,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
