"""Optional static Top5 section for the existing ARVIS HTML dashboard."""
from decimal import Decimal,InvalidOperation
from html import escape
import os
import re
from pathlib import Path
from urllib.parse import quote
from .analyst.saved import read_json
from .daily_chart import fingerprint

CSS='''#c12-top5{padding:16px 18px;max-width:1440px;margin:auto;overflow-wrap:anywhere}#c12-top5 .top5-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}#c12-top5 .top5-card{min-width:0;border:1px solid #dbe3eb;border-radius:10px;padding:14px;background:#fff}#c12-top5 h3{font-size:17px;margin:0 0 8px}#c12-top5 p,#c12-top5 li{font-size:13px;line-height:1.5}#c12-top5 img{display:block;width:100%;height:auto}#c12-top5 .top5-state{color:#805b10}#c12-top5 .top5-metrics{display:flex;flex-wrap:wrap;gap:8px 16px}#c12-top5 .top5-demo{background:#fff4df;padding:10px;border-radius:6px;font-weight:600}@media(max-width:760px){#c12-top5 .top5-grid{grid-template-columns:1fr}}'''


def money(value):
    try:
        n=Decimal(str(value))
        return f'{n:,.0f}' if n.is_finite() else 'unavailable'
    except (InvalidOperation,TypeError,ValueError):return 'unavailable'


COMPACT_CSS = """#c12-top5.top5-compact .top5-card{padding:10px 12px}
#c12-top5.top5-compact h3{font-size:16px;margin-bottom:3px}
#c12-top5.top5-compact .compact-rank{font-size:12px;color:#64748b;margin:0 0 5px}
#c12-top5.top5-compact .compact-chart{display:block;aspect-ratio:960/370}
#c12-top5.top5-compact .compact-chart img{width:100%;height:100%;object-fit:contain}
#c12-top5.top5-compact .compact-summary p{margin:2px 0;font-size:13px;line-height:1.4}
#c12-top5.top5-compact .compact-verdict{font-weight:700;color:#334155}"""
COMPACT_CSS+='''#c12-top5.top5-compact .top5-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
@media(max-width:1100px){#c12-top5.top5-compact .top5-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px){#c12-top5.top5-compact .top5-grid{grid-template-columns:1fr}}'''


def compact_summary(card):
    card=card or {};review=card.get('position_review') or {}
    support=(card.get('mini_chart_metadata') or {}).get('support_zone') or {}
    def price(value):return money(value) if value is not None else '미확인'
    def zone(low,high):
        return price(low) if high is None or low==high else price(low)+'~'+price(high)
    def distance(value):
        try:return format(Decimal(value),'.2f')+'%' if value is not None else '미확인'
        except (InvalidOperation,TypeError,ValueError):return '미확인'
    low=support.get('low',(review.get('nearest_support') or {}).get('value'))
    resistance='Resistance '+zone(card.get('resistance_zone_low'),card.get('resistance_zone_high'))
    nearest=(review.get('nearest_resistance') or {}).get('value')
    if nearest is not None:resistance+=' (최근접 '+price(nearest)+')'
    lines=[resistance,
           'Support '+zone(low,support.get('high')),
           '저항 거리 '+distance(review.get('resistance_distance_percent'))+' / 지지 거리 '+distance(review.get('support_distance_percent')),
           review.get('observation_type','데이터 부족')]
    return '<div class="compact-summary">'+''.join('<p'+(' class="compact-verdict"' if i==3 else '')+'>'+escape(line)+'</p>' for i,line in enumerate(lines))+'</div>'


def local_asset(base,value):
    if not isinstance(value,str):raise ValueError('local_path_required')
    path=Path(value)
    if path.is_absolute() or '..' in path.parts:raise ValueError('unsafe_asset_path')
    path=(base/path).resolve()
    if not path.is_relative_to(base.resolve()):raise ValueError('asset_outside_bundle')
    return path


def position_only(card):
    review=(card or {}).get('position_review') or {}
    distance=review.get('resistance_distance_percent')
    text=format(Decimal(distance),'.2f')+'%' if distance is not None else '미확인'
    original=review.get('observation_type','데이터 부족')
    verdict={'돌파 관찰':'관찰','눌림 관찰':'눌림 후보','저항 근접·추격 주의':'저항 근접 주의'}.get(original,original)
    if original!='데이터 부족':verdict='상승 관측 · '+('일봉 추세 약화' if original=='추세 약화' else verdict)
    return '<div class="compact-summary"><p title="저장 종가에서 C16 최근접 상단 후보까지의 기존 거리">전고점 이격 '+text+'</p><p class="compact-verdict">'+escape(verdict)+'</p></div>'


def default_chart_items(items):
    quotas={1:3,2:3,3:3};seen=set();visible=[];remaining=[]
    for item in sorted(items,key=lambda i:(i.get('c12_sector_rank',999),i.get('c12_leader_rank',999))):
        if (item.get('c12_leader_rank',999)<=quotas.get(item.get('c12_sector_rank'),0)
                and item['stock_code'] not in seen and len(visible)<9):
            visible.append(item);seen.add(item['stock_code'])
        else:remaining.append(item)
    return visible,remaining


def top5_section(top5_json,output_html,*,limit=5):
    source=Path(top5_json).resolve();report,_=read_json(source)
    if report.get('schema_version')!='arvis-c12-chart-top5-v1' or report.get('source_kind') not in ('fixture','readonly_adapter'):
        raise ValueError('saved_top5_schema_required')
    demo=report['source_kind']=='fixture';output=Path(output_html).resolve()
    label='C15' if report.get('selection_source')=='C15' else 'C12'
    heading='\uc8fc\ub3c4\uc8fc Top5 \ucc28\ud2b8 \ubd84\uc11d'
    if label=='C15':heading='C15 주도주 일봉 위치'
    compact=label=='C15'
    minimal=report.get('card_ui')=='position_only'
    parts=[f'<section id="c12-top5"'+(' class="top5-compact"' if compact else '')+f'><h2>{heading}</h2>']
    if demo:parts.append('<p class="top5-demo">DEMO / SYNTHETIC — test data, not actual market leaders or recommendations.</p>')
    if minimal:
        parts.append('<p>장중 관측 '+escape(str(report.get('quote_at') or '미확인'))+' · 저장 일봉 '+escape(', '.join(report.get('daily_dates',[])) or '미확인')+' · 차트 분석 '+escape(str(report.get('generated_at')) )+'</p><div class="top5-grid">')
    elif compact:
        parts.append('<p>C15 '+escape(str(report.get('c12_as_of')))+' · C16 '+escape(str(report.get('generated_at')))+' · 저장 일봉 참고</p><div class="top5-grid">')
    else:
        parts.append('<p>C12 snapshot: '+escape(str(report.get('c12_as_of')))+' | Generated: '+escape(str(report.get('generated_at')))+'. Existing sector / leader order. Saved chart data, not live quotes.</p><div class="top5-grid">')
    previous_sector=None
    items=report.get('items',[])[:limit]
    visible,remaining=default_chart_items(items) if minimal else (items,[])
    for item_index,item in enumerate(visible+remaining):
        if remaining and item_index==len(visible):
            parts.append('<details class="chart-remainder" style="grid-column:1/-1"><summary>나머지 대표 종목 · 중복 소속 원본</summary><div class="top5-grid">')
            previous_sector=None
        if minimal and item.get('c12_sector_rank')!=previous_sector:
            previous_sector=item.get('c12_sector_rank')
            parts.append('<h3 class="chart-sector-title" data-sector-rank="'+escape(str(previous_sector),quote=True)+'">'+escape(str(previous_sector))+'위 '+escape(str((item.get('summary') or ['미확인'])[0]))+'</h3>')
        code=item.get('stock_code');name=item.get('stock_name') or code
        if compact:
            parts.append(f'<article class="top5-card" data-code="{escape(str(code),quote=True)}" data-sector-rank="{escape(str(item.get("c12_sector_rank")),quote=True)}"><h3>{escape(str(name))}'+('' if minimal else ' <small>'+escape(str(code))+'</small>')+'</h3>')
            if not minimal:parts.append('<p class="compact-rank">C15 sector rank '+escape(str(item.get('c12_sector_rank')))+' / leader rank '+escape(str(item.get('c12_leader_rank')) )+'</p>')
        else:
            parts.append(f'<article class="top5-card" data-code="{escape(str(code),quote=True)}"><h3>#{escape(str(item.get("rank")))} {escape(str(name))} <small>{escape(str(code))}</small></h3>')
            evidence=item.get('c12_evidence') or {}
            parts.append('<p>'+label+': sector rank '+escape(str(item.get('c12_sector_rank')))+' / leader '+escape(str(item.get('c12_leader_rank')))+' | latest rank '+escape(str(item.get('c12_latest_rank')))+' | Top5 appearances '+escape(str(evidence.get('top5_appearances','unavailable')) )+'</p>')
        card=None
        try:
            if not isinstance(code,str):raise ValueError('string_code_required')
            card,_=read_json(local_asset(source.parent,item.get('card_path')))
            if (card.get('schema_version')!='arvis-chart-card-v1' or card.get('stock_code')!=code
                or card.get('environment')!=report['environment'] or card.get('data_origin')!=('synthetic_fixture' if demo else 'api_response')):raise ValueError('card_scope')
            if card.get('card_reference')!=fingerprint({k:v for k,v in card.items() if k!='card_reference'}):raise ValueError('card_fingerprint')
        except (OSError,ValueError,TypeError,KeyError):
            card=None
        if card is None and compact:
            parts.append('<p class="top5-state">차트 미확인</p>'+(position_only(None) if minimal else compact_summary(None)))
        elif card is None:
            parts.append('<p class="top5-state">Analysis unavailable / failed: '+escape(str(item.get('reason') or 'card_missing_or_invalid'))+'</p>')
        else:
            if compact and not minimal:
                saved=card.get('mini_chart_metadata') or {}
                parts.append('<p class="compact-close">종가 '+(money(saved['latest_close']) if saved.get('latest_close') is not None else '미확인')+' · '+escape(str(saved.get('latest_date') or '기준일 미확인'))+'</p>')
            image=None
            try:
                image=local_asset(source.parent,item.get('mini_chart_path'))
                if image.suffix.lower()!='.png' or not image.is_file():image=None
            except (ValueError,TypeError):pass
            if image:
                url=escape(quote(os.path.relpath(image,output.parent).replace('\\','/'),safe='/'),quote=True)
                parts.append(f'<a class="{"compact-chart" if compact else "chart-link"}" href="{url}" target="_blank" rel="noopener"><img src="{url}" alt="{escape(str(name),quote=True)} 60-session chart; click for original"></a>')
            else:parts.append('<p class="top5-state">Chart not generated / PNG missing</p>')
            if compact:
                parts.append(position_only(card) if minimal else compact_summary(card))
                series=card.get('mini_chart_metadata') or {}
                zone=series.get('resistance_zone') or {}
                kind='구조형 가격 영역' if zone.get('source')=='bull_close_next_observed_bear_upper_wick' else '일봉 기반 계산형 추정 영역'
                if not minimal:parts.append('<details class="compact-evidence"><summary>근거 · '+escape(str(zone.get('status') or 'unavailable'))+'</summary><p>'+kind+' · 실제 가격대별 거래량 아님</p>'+''.join('<p>'+escape(str(line))+'</p>' for line in item.get('summary',[]))+'</details>')
            else:
                provenance=card.get('provenance',{});observed=provenance.get('observed_daily') or {}
                close=observed.get('reference_close') or provenance.get('daily_reference_close') or {}
                parts.append('<p>Close '+money(close.get('value'))+' KRW | Data date '+escape(str(close.get('trade_date','unavailable')))+' | Analysis '+escape(str(card.get('as_of')) )+'</p>')
                parts.append('<div class="top5-metrics">')
                for key in ('MA5','MA20','MA60'):
                    ma=card.get('moving_averages',{}).get(key,{})
                    parts.append('<span>'+key+' '+money(ma.get('value'))+' / '+escape(str(ma.get('position') or 'unavailable'))+' ('+escape(str(ma.get('status','unavailable')))+')</span>')
                parts.append('</div>')
                trend=card.get('daily_trend',{})
                parts.append('<p>MA20 above MA60: '+escape(str(trend.get('ma20_above_ma60','unavailable')))+' | State: '+escape(str(item.get('status')) )+'</p>')
                if card.get('resistance_zone_low') is not None:
                    parts.append('<p>Resistance '+money(card['resistance_zone_low'])+'–'+money(card.get('resistance_zone_high'))+' KRW | '+escape(str(card.get('resistance_zone_source')))+' ('+escape(str(card.get('resistance_zone_status')))+')</p>')
                if card.get('position_review'):
                    review=card['position_review']
                    parts.append('<p><strong>'+escape(review['observation_type'])+'</strong> · '+escape(review['summary'])+'</p>')
                parts.append('<ul>'+''.join('<li>'+escape(re.sub(r'-?\d+\.\d{3,}',lambda m:format(Decimal(m.group()),'.2f'),str(line)))+'</li>' for line in item.get('summary',[]))+'</ul>')
        parts.append('</article>')
    if remaining:parts.append('</div></details>')
    if not report.get('items'):parts.append('<p>No eligible C12 candidates available.</p>')
    parts.append('</div>'+('<p>저장 일봉 추정 영역 · 실제 가격대별 거래량 아님</p>' if compact else '<p>No automated orders or confirmed buy signals. Provisional values remain unverified.</p>')+'</section>')
    return '<style>'+CSS+(COMPACT_CSS if compact else '')+'</style>'+''.join(parts)


def attach_top5(html,top5_json,output_html,*,limit=5):
    section=top5_section(top5_json,output_html,limit=limit)
    # Existing offline CSP blocks images; allow only same-origin saved assets.
    if "default-src 'none';" in html and 'img-src ' not in html:
        html=html.replace("default-src 'none';","default-src 'none'; img-src 'self';",1)
    marker='</main>' if '</main>' in html else '</body>'
    return html.replace(marker,section+marker,1)
