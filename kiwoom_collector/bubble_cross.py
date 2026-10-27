"""Read-only Bubble extension consuming saved Step 5-E JSON, never databases/APIs."""
import argparse
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json

from . import bubble_layout as layout, bubble_timeline as timeline
from . import cross_adapter as adapter
from .radar import assess
from .radar_v2 import e, summarize


def build(document,*,capital_records=None,capital_proofs=None):
    """Preserve Bubble geometry/interest and isolate financial data to its pair."""
    a,b=document['ranking'],document['turnover']
    if a['environment']!=b['environment']:raise ValueError('Mixed environments forbidden')
    observations=sorted([*document.get('history',[]),a],key=lambda r:adapter.timestamp(r['collected_at']))
    runs=[]
    for sequence,r in enumerate(observations,1):
        if r['environment']!=a['environment']:raise ValueError('Mixed history environment')
        stocks=[]
        for source in r['rows']:
            tags=[dict(stock_code=source['stk_cd'],sector_name=t['sector_name'],source=t.get('source','saved metadata')) for t in document.get('sectors',{}).get(source['stk_cd'],[])]
            stocks.append(dict(rank=source['rank'],stk_cd=source['stk_cd'],stk_nm=source['stk_nm'],
                               quote=assess(None,r['collected_at']),observations=[],tags=tags))
        runs.append(dict(r,sequence=sequence,test_mode=int(r['test_mode']),stocks=stocks,quote_batches=[]))
        runs[-1]['sectors']=summarize(runs[-1])['sectors']
    legacy=dict(date=adapter.timestamp(a['collected_at']).date().isoformat(),environment=a['environment'],db='saved Step 5-E JSON',runs=runs)
    data=layout.optimize(timeline.build(legacy,runs))
    comparisons={n:adapter.analyze_pair(document,min_turnover_eok=n) for n in (500,1000)}
    if capital_records is not None:
        from .capital_filter import integrate
        comparisons={n:integrate(r,capital_records,proofs=capital_proofs) for n,r in comparisons.items()}
    return dict(timeline=data,comparisons=comparisons,source=deepcopy(document))


def money(value):
    amount=adapter.number(value)
    if amount is None or amount<0:return '미확인'
    if amount>=Decimal('1000000000000'):return f'{amount/Decimal("1000000000000"):,.2f}조원'
    return f'{amount/Decimal("100000000"):,.2f}억원'


def ordered(members,rows):
    def key(s):
        r=rows.get(s['code'],{})
        maintained=s['rank'] is not None and s['rank']<=5 and s.get('previous_rank') is not None and s['previous_rank']<=5
        return (s['rank'] is None or s['rank']>5,-s.get('top5_count',r.get('top5_appearances',0)),not maintained,r.get('turnover_rank') or 10**9,s['rank'] or 99,s['code'])
    return sorted(members,key=key)


def card(stock,row,active):
    row=row or {}
    available=active and bool(row)
    amount=money(row.get('turnover_won_diagnostic')) if available else '미확인'
    rate=row.get('change_percent_raw') if available else None
    rank=row.get('turnover_rank') if available else None
    repeat=stock.get('top5_count',row.get('top5_appearances'))
    status='검증 보류' if available else '이 회차의 교차 자료 없음' if not active else '코드·자료 검토 보류'
    qualification=row.get('qualification',{}) if active else {}
    checks=qualification.get('checks',{})
    label=lambda value:'확인(근거 검증)' if value is True else '미달(근거 검증)' if value is False else '미확인'
    cap_label=label(checks.get('market_cap_3000_eok'))
    closing_label=label(checks.get('closing_turnover_1000_eok'))
    capital=row.get('capital_reference') if active else None
    capital_note=('시총 별도 조회 '+str(capital.get('retrieved_at'))+' · 선택 회차 적용 미검증') if capital else ''
    band_labels=dict(primary_exploration='핵심 탐색 3,000억원~5조원',extended_exploration='확장 탐색 5조 초과~10조원',mega_cap_leader='초대형 주도주 10조원 초과',below_floor='하한 미만',unknown='미확인')
    if active and row.get('capital_band'):
        capital_note+=' · 시총 구간: '+band_labels[row['capital_band']['band']]+' (잠정 전략 가설 · 상한 제외 없음)'
    return (f'<article class="stock-card" data-code="{e(stock["code"])}" data-confirmed="false">'
        f'<header><b>{e(stock["name"] or stock["code"])}</b><code>{e(stock["code"])}</code></header>'
        f'<div class="stock-metrics"><span>0198 관심순위<b>{e(stock["rank"])}위</b></span>'
        f'<span>당일 TOP5 출현<b>{e(repeat) if repeat is not None else "미확인"}{"회" if repeat is not None else ""}</b></span>'
        f'<span>0186 거래대금순위<b>{e(rank) if rank else "미확인"}{"위" if rank else ""}</b></span>'
        f'<span>장중 거래대금 · 명세 환산<b>{e(amount)}</b></span>'
        f'<span>등락률 · 원문 잠정<b>{e(rate)+"%" if rate is not None else "미확인"}</b></span>'
        f'<span>시총 3,000억원 이상<b>{e(cap_label)}</b></span></div>'
        f'<p class="condition">장 마감 누적 1,000억원 이상: <b>{e(closing_label)}</b> · 장중 관측으로 판정하지 않음</p>'
        + (f'<small class="condition">{e(capital_note)}</small>' if capital_note else '')+
        f'<small class="stock-status">{e(status)} · 후보 확정 아님</small></article>')


CSS='''
.bubble-layout{grid-template-columns:minmax(0,1.25fr) minmax(0,1fr)}
.cross-section{border:1px solid #c9d7e4;border-radius:12px;background:#fff;padding:12px;min-width:0;overflow-wrap:anywhere}
.cross-section h2{font-size:17px}.cross-subtitle,.cross-note{font-size:11px;color:#5c6d80;line-height:1.5;margin:5px 0}
.cash-controls{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.cash-controls label{border:1px solid #cbd6e2;border-radius:6px;padding:7px 9px;cursor:pointer;font-size:11px}
.bubble-root:has(#cash-1000:checked) label[for=cash-1000],.bubble-root:has(#cash-500:checked) label[for=cash-500]{background:#26394d;color:white}
.cross-sector,.cash-variant{display:none}.bubble-root:has(#cash-1000:checked) .cash1000,.bubble-root:has(#cash-500:checked) .cash500{display:block}
.stock-card{border:1px solid #dde5ee;border-radius:8px;padding:9px;margin:8px 0;background:#fafcfe;min-width:0}
.stock-card header{display:flex;justify-content:space-between;gap:8px;align-items:start}.stock-card header b{font-size:13px;overflow-wrap:anywhere;min-width:0}.stock-card code{font-size:10px;white-space:nowrap;color:#647589}
.stock-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:8px}.stock-metrics span{font-size:9px;color:#617185;overflow-wrap:anywhere}.stock-metrics b{display:block;font-size:12px;color:#26394d;font-variant-numeric:tabular-nums}
.condition,.stock-status{font-size:10px;line-height:1.45;color:#647184}.condition{margin:8px 0 3px}.cross-section summary{cursor:pointer;font-size:11px;line-height:1.5}.cross-evidence{border-top:1px solid #e3e9f0;margin-top:8px;padding-top:8px;font-size:10px}.cross-evidence code{display:block;overflow-wrap:anywhere}
.cross-aux{margin-top:8px}.cross-aux .stock-card{background:white}.cross-empty{padding:10px;background:#f2f5f9;border-radius:6px;font-size:12px}.cross-filter-count{font-size:10px;color:#647589}
.cross-unclassified-label{display:inline-block;margin:7px 0;padding:5px;border:1px solid #d6dfe9;border-radius:5px;cursor:pointer;font-size:11px}
@media(max-width:850px){.bubble-layout{grid-template-columns:1fr}.sidebar{grid-template-columns:1fr}.cross-section{order:-1}.stock-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:540px){.stock-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.stock-card header{flex-wrap:wrap}.cross-section{padding:10px}.cash-controls label{flex:1;min-width:0}.bubble-root h2{font-size:16px}}
'''


def frame_html(frame,bundle):
    html=layout.frame_html(frame)
    doc=bundle['source'];a,b=doc['ranking'],doc['turnover']
    bound=frame['collection_id']==a['id']
    active=bound and bundle['comparisons'][1000]['timing']['collection_window_provisional']
    repeats={s['code']:s['top5_appearances'] for s in frame['data']['repeated_stocks']}
    group_members=[(g['id'],g['name'],[dict(s,top5_count=repeats.get(s['code'],0)) for s in g['members'] if s['rank'] is not None]) for g in frame['model']['groups']]
    current=frame['data']['current']
    unclassified=[dict(code=s['stk_cd'],name=s['stk_nm'],rank=s['rank'],status='미분류',top5_count=repeats.get(s['stk_cd'],0)) for s in current['stocks'] if not s['tags']] if current else []
    group_members.append(('unclassified','미분류',unclassified))
    radio='<input class="sr-only sector-choice" type="radio" name="sector" id="pick-unclassified" aria-label="미분류 상세 선택">'
    html=html.replace('<div class="bubble-layout">',radio+'<div class="bubble-layout">',1)
    html=html.replace('<p class="map-foot">','<label class="cross-unclassified-label" for="pick-unclassified">미분류 '+str(len(unclassified))+'종목 · 교차 자료 보기</label><p class="map-foot">',1)
    selectors=''.join(f'.bubble-root:has(#pick-{gid}:checked) #cross-{gid}'+'{display:block}' for gid,_,_ in group_members)
    parts=['<section class="cross-section" aria-label="0198 0186 핵심 종목 교차 분석"><h2>선택 섹터 · 핵심 종목</h2>',
        '<p class="cross-subtitle">TOP20은 섹터 탐지 · TOP5는 우선 검토 · 자동 후보 확정 없음</p>',
        '<div class="cash-controls" role="group" aria-label="잠정 거래대금 필터"><input class="sr-only" type="radio" name="cash" id="cash-1000" checked><label for="cash-1000">기본 · 1,000억원</label><input class="sr-only" type="radio" name="cash" id="cash-500"><label for="cash-500">보조 · 500억원</label></div>',
        '<p class="cross-note">필터는 장중 잠정 금액 · 0186 40위 이내. 장 마감 누적 조건과 다릅니다.</p>']
    if not active:
        message=('시차 300초 기준 미충족 · 교차 금액 표시 보류' if bound else '이 회차에 결합된 0186 자료 없음 · 15:03 자료를 과거에 소급하지 않습니다.')
        parts.append('<p class="cross-empty">'+message+'</p>')
    for gid,name,members in group_members:
        parts.append(f'<div class="cross-sector" id="cross-{gid}" data-sector="{e(name)}"><h3>{e(name)}</h3>')
        for n in (1000,500):
            report=bundle['comparisons'][n];rows={r['stock_code']:r for r in report['rows']} if active else {}
            sorted_members=ordered(members,rows)
            core=[s for s in sorted_members if s['rank']<=5 and rows.get(s['code'],{}).get('filter_selected')]
            auxiliary=[s for s in sorted_members if s not in core]
            parts.append(f'<div class="cash-variant cash{n}" data-threshold="{n}"><p class="cross-filter-count">이 섹터 TOP20 {len(members)}개 · 잠정 필터 통과 '+str(sum(rows.get(s['code'],{}).get('filter_selected',False) for s in members))+f'개 · TOP5 우선 {len(core)}개</p>')
            parts.extend(card(s,rows.get(s['code']),active) for s in core)
            if not core:parts.append('<p class="cross-empty">검토 가능한 TOP5 교차 자료 없음</p>')
            parts.append(f'<details class="cross-aux"><summary>TOP20 보조 관찰 · 필터 제외 · 코드 검토 {len(auxiliary)}종목</summary>')
            parts.extend(card(s,rows.get(s['code']),active) for s in auxiliary)
            parts.append('</details></div>')
        parts.append('</div>')
    delta=bundle['comparisons'][1000]['timing']['delta_seconds']
    parts.append('<details class="cross-evidence"><summary>시차·출처·검증 근거</summary>'+f'<p>선택 0198: {e(frame["collected_at"])}<br>결합 대상 0198: {e(a["collected_at"])}<br>0186 요청: {e(b.get("request_started_at"))}<br>0186 응답: {e(b.get("response_received_at"))}</p>'+f'<p>결합 쌍 시차 {e(delta)}초 · 300초 잠정 기준. '+('이 회차에만 적용.' if active else '현재 회차에는 미적용.')+'</p><p>시장 기준 시각·시장 조건·HTS 단위·시총·장 마감 금액: 미확인. 섹터 동반 상승 검증: 미확인. candidate_confirmed=false.</p><p>mock/real 환경은 혼합하지 않음 · 운영 0198 × 테스트 0186 진단 전용 · 100개 첫 페이지/partial.</p>'+f'<code>0198 {e(a["id"])}</code><code>0186 {e(b["id"])}</code></details></section>')
    html=html.replace('<aside class="sidebar">','<aside class="sidebar">'+''.join(parts),1)
    return html.replace('</head>','<style>'+CSS+selectors+'</style></head>',1)


def render(bundle):
    return timeline.render(bundle['timeline'],frame_renderer=lambda frame:frame_html(frame,bundle)).replace('<title>STOCK1 · Bubble Radar 장중 타임라인</title>','<title>STOCK1 · Bubble Radar × 0198·0186</title>',1)


def export(document,output):
    target=Path(output)
    if target.suffix.lower()!='.html' or target.exists() or target.is_symlink():raise ValueError('New HTML path required')
    result=build(document)
    html=render(result)
    with target.open('x',encoding='utf8') as f:f.write(html)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-json',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--capital-json',type=Path)
    args=p.parse_args(argv)
    if args.input_json.suffix.lower()!='.json':p.error('Saved JSON input required')
    document=json.loads(args.input_json.read_text(encoding='utf-8-sig'))
    if args.capital_json:
        if args.capital_json.suffix.lower()!='.json':p.error('Saved capital JSON required')
        records=json.loads(args.capital_json.read_text(encoding='utf-8-sig'))
        records=records if isinstance(records,list) else [records]
        if args.output.exists() or args.output.suffix.lower()!='.html':p.error('New HTML output required')
        html=render(build(document,capital_records=records))
        with args.output.open('x',encoding='utf8') as f:f.write(html)
    else:export(document,args.output)
    return 0


if __name__=='__main__':raise SystemExit(main())
