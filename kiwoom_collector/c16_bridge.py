"""C15 selected leaders -> existing C14 cards/mini PNGs and existing dashboard."""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

from . import krx_c14,eligibility_dashboard,cross_adapter,top5_dashboard
from .analyst.mini_chart import chart_series,render_png
from .daily_chart import fingerprint

ROOT=Path(__file__).resolve().parents[1]
RULES=dict(status='initial_observation_hypothesis',near_resistance_percent='2',pullback_ma20_percent='3',
           order_signal=False,score_applied=False)


def position(series,observed):
    bars=series['bars'];close=Decimal(series['latest_close'])
    lows=[];highs=[];bulls=[];wicks=[]
    for i,b in enumerate(bars):
        o,h,l,c=(Decimal(b[k]) for k in ('open','high','low','close'))
        if 0<i<len(bars)-1:
            if l<=Decimal(bars[i-1]['low']) and l<=Decimal(bars[i+1]['low']):
                lows.append(dict(value=str(l),trade_date=b['trade_date'],kind='observed_pivot_low',high=str(min(o,c))))
            if h>=Decimal(bars[i-1]['high']) and h>=Decimal(bars[i+1]['high']):
                highs.append(dict(value=str(h),trade_date=b['trade_date'],kind='observed_pivot_high'))
        if c>o and (c/o-1)*100>=3 and h>l and (c-o)/(h-l)>=Decimal('.6'):
            bulls.append(dict(trade_date=b['trade_date'],close=str(c),high=str(h)))
        if h>max(o,c) and h>l and (h-max(o,c))/(h-l)>=Decimal('.3'):
            wicks.append(dict(trade_date=b['trade_date'],low=str(max(o,c)),high=str(h)))
    supports=[b for b in lows if Decimal(b['value'])<close]
    support=max(supports,key=lambda b:Decimal(b['value']),default=None)
    if support:
        low=Decimal(support['value']);high=min(Decimal(support['high']),low*Decimal('1.0075'),close)
        series['support_zone']=dict(low=str(low),high=str(high),trade_date=support['trade_date'],
            source='daily_pivot_low_body_edge_capped_0.75_percent',status='provisional')
    candidates=[b for b in highs if Decimal(b['value'])>close]
    for b in wicks:
        if Decimal(b['low'])>close:candidates.append(dict(value=b['low'],trade_date=b['trade_date'],kind='upper_wick_body_edge'))
    zone=series['resistance_zone']
    if zone.get('low') and Decimal(zone['low'])>close:
        candidates.append(dict(value=zone['low'],kind=zone['source'],trade_date=(zone.get('bear') or {}).get('trade_date')))
    resistance=min(candidates,key=lambda b:Decimal(b['value']),default=None)
    if resistance:
        series['nearest_resistance_candidate']=resistance
        if series['resistance_zone']['status']=='unavailable':
            value=Decimal(resistance['value'])
            series['resistance_zone']=dict(status='provisional',low=str(value*Decimal('.9925')),
                high=str(value*Decimal('1.0075')),source='observed_pivot_or_wick_calculated_band',
                evidence=resistance,half_width_percent='0.75')
    series['resistance_evidence_dates']=[a['trade_date'] for a in (observed.get('supply_proxy') or {}).get('areas',[])]
    up=(Decimal(resistance['value'])/close-1)*100 if resistance else None
    down=(1-Decimal(support['value'])/close)*100 if support else None
    ma=observed['moving_averages'];m20=ma['MA20'];m60=ma['MA60']
    if len(bars)<60 or not m20['value'] or not m60['value']:state='데이터 부족'
    elif close<Decimal(m20['value']) and Decimal((m20.get('slope') or {}).get('delta') or '0')<0:state='추세 약화'
    elif up is not None and up<=Decimal(RULES['near_resistance_percent']):state='저항 근접·추격 주의'
    elif close>=Decimal(m20['value']) and abs((close/Decimal(m20['value'])-1)*100)<=Decimal(RULES['pullback_ma20_percent']):state='눌림 관찰'
    elif close>=Decimal(m20['value']) and Decimal(m20['value'])>=Decimal(m60['value']):state='돌파 관찰'
    else:state='추세 약화'
    return dict(observation_type=state,rules=RULES,reference_close=observed['reference_close'],
        current_intraday_price=None,current_price_status='unavailable_saved_daily_close_only',
        moving_averages={k:ma[k] for k in ('MA5','MA20','MA60')},recent_highs=highs[-5:],recent_lows=lows[-5:],
        long_bull_candles=bulls[-5:],upper_wick_areas=wicks[-5:],nearest_resistance=resistance,nearest_support=support,
        resistance_distance_percent=str(up) if up is not None else None,support_distance_percent=str(down) if down is not None else None,
        high52=observed.get('high52'),
        volume_at_price_available=False,volume_notice='일봉 OHLCV 구조형/계산형 추정 영역. 실제 가격대별 거래량·잔존 매물 아님.',
        summary='저항 거리 '+(f'{up:.2f}%' if up is not None else '미확인')+' / 지지 거리 '+(f'{down:.2f}%' if down is not None else '미확인')+' · 저장 일봉 기준 잠정 관찰')


def build(source,*,daily_db,as_of,output):
    from .am_time_policy import instant
    if instant(as_of)<instant(source['reference_at']):raise ValueError('candidate_not_known_at_chart_time')
    output=Path(output).absolute()
    if output.exists() or not output.resolve().is_relative_to(ROOT/'diagnostics'):raise ValueError('new_development_diagnostics_directory_required')
    output.mkdir(parents=True)
    sectors=source['analysis']['sectors']
    candidates=[(sector,leader) for sector in sectors[:3] for leader in sector['leaders'][:3]]
    codes=list(dict.fromkeys(s['stock_code'] for _,s in candidates));rows,index=krx_c14.read_inputs(codes,daily_db)
    cards={};items=[]
    for sector,leader in candidates:
        code=leader['stock_code'];folder=output/code
        if code not in cards:
            folder.mkdir()
            result=krx_c14.make_card(rows[code],index,code=code,name=leader['name'],as_of=as_of)
            card=result['card'];observed=result['observed']
            try:
                series=chart_series(card,observed);review=position(series,observed)
                original_estimate=deepcopy(card.get('resistance_estimate'))
                render_png(series,card,folder/'mini.png')
                card.update(position_review=review,mini_chart_metadata=series,mini_chart_path='mini.png',
                    mini_chart_status='available' if series['candle_count']==60 else 'partial',
                    resistance_zone_low=series['resistance_zone']['low'],resistance_zone_high=series['resistance_zone']['high'],
                    resistance_zone_source=series['resistance_zone']['source'],resistance_zone_status=series['resistance_zone']['status'])
                assert card.get('resistance_estimate')==original_estimate
            except ValueError as error:
                card.update(mini_chart_status='unavailable',mini_chart_path=None,
                    position_review=dict(observation_type='데이터 부족',summary=type(error).__name__))
            card['card_reference']=fingerprint({k:v for k,v in card.items() if k!='card_reference'})
            for name,obj in [('card',card),('observed',observed),('input',result['input'])]:
                (folder/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
            cards[code]=card
        card=cards[code]
        items.append(dict(rank=len(items)+1,stock_code=code,stock_name=leader['name'],c12_sector_rank=sector['rank'],c12_leader_rank=len([x for x in items if x['c12_sector_rank']==sector['rank']])+1,
            status=card.get('mini_chart_status'),card_path=code+'/card.json',mini_chart_path=code+'/mini.png' if card.get('mini_chart_path') else None,
            summary=[sector['name'],card['position_review']['summary'],'일봉 OHLCV 추정 영역 · 실제 가격대별 거래량 아님']))
    report=dict(schema_version='arvis-c12-chart-top5-v1',source_kind='readonly_adapter',selection_source='C15',environment='real',
        c12_as_of=source['reference_at'],generated_at=as_of,items=items,chart_source=str(daily_db),
        notice='C15 후보 시각과 차트 분석 시각을 분리. 과거 시점에 나중 자료를 혼합하지 않음.')
    (output/'top5.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    raw=source['diagnostics']['source_report'];rank=raw['analysis']['snapshot_selection']['selected']['0198']['snapshot']
    cross=cross_adapter.adapt(raw['paths']['ranking'],raw['paths']['turnover'],ranking_at=rank['collection_started_at'],environment='real')
    html=eligibility_dashboard.render(eligibility_dashboard.build(cross))
    html=top5_dashboard.attach_top5(html,output/'top5.json',output/'dashboard.html',limit=9)
    from .c16_ui import attach_chart_news
    html=attach_chart_news(html,source['analysis']['sectors'],source['reference_at'])
    (output/'dashboard.html').write_text(html,encoding='utf8')
    text=[]
    for item in items:
        card=cards[item['stock_code']];r=card['position_review']
        text.append(item['stock_name']+' '+item['stock_code']+' | '+r['observation_type']+' | '+r['summary'])
    (output/'summary.txt').write_text('\n'.join(text),encoding='utf8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--briefing',type=Path,required=True);p.add_argument('--daily-db',type=Path,required=True)
    p.add_argument('--as-of',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();source=json.loads(a.briefing.read_text(encoding='utf8'))
    if source.get('schema_version')=='c15-morning-delivery-v1':
        full=a.briefing.with_name('diagnostics.json')
        if not full.is_file():raise ValueError('full_c15_diagnostics_required_beside_portable_telegram_report')
        source=json.loads(full.read_text(encoding='utf8'))
    report=build(source,daily_db=a.daily_db,as_of=a.as_of,output=a.output)
    print(json.dumps(dict(cards=len(report['items']),output=str(a.output),api_calls=0),ensure_ascii=False))


if __name__=='__main__':main()
