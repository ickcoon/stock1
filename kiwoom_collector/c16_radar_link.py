"""Bind saved C15 snapshots and C16 cards to the existing Radar frames. No scoring."""
from copy import deepcopy
from decimal import Decimal
from html import escape
import json
from pathlib import Path
import shutil

from . import cross_adapter,eligibility_dashboard,bubble_timeline,c15_identity,c15_leadership,top5_dashboard
from .c12_external_theme_bridge import load
from .c15_briefing_data import format_capital
from .am_time_policy import instant

ROOT=Path(__file__).resolve().parents[1]


def mapped_document(document,records,official,as_of):
    """Reuse C15 exact identity mapping for only the selected rank prefix."""
    result=deepcopy(document)
    watched={r['stk_cd']:dict(stock_code=r['stk_cd'],name=r['stk_nm'])
        for run in [*result['history'],result['ranking']] for r in run['rows']}
    tags,_=c15_leadership.map_themes(list(watched.values()),records,as_of[:10],official_records=official,as_of=as_of)
    result['sectors']={}
    for tag in tags:
        values=result['sectors'].setdefault(tag['stock_code'],[])
        if not any(v['sector_name']==tag['sector_name'] for v in values):values.append(tag)
    return result


def cards_for_snapshot(snapshot,pool,asset_root,output,slot):
    """Retain C15 ranks; attach only matching saved C16 cards, never substitute names."""
    lookup={i['stock_code']:i for i in pool['items']};items=[]
    for sector in snapshot['analysis']['sectors'][:3]:
        for leader_rank,leader in enumerate(sector['leaders'][:3],1):
            code=leader['stock_code'];item=deepcopy(lookup.get(code,{}))
            item.update(rank=len(items)+1,stock_code=code,stock_name=leader['name'],
                c12_sector_rank=sector['rank'],c12_leader_rank=leader_rank)
            m=snapshot['diagnostics']['stock_measurements'].get(code,{})
            cap=m.get('capital',{});cash=m.get('turnover',{})
            summary=[sector['name'],f"등락률 {leader['raw_change']}% / "+format_capital(cap.get('won')),
                     '거래대금 '+(str(cash['eok'])+'억' if cash.get('eok') is not None else '미확인'),
                     '시총 기준일 '+str(cap.get('basis_date') or '미확인')+' / 대금 수집 '+str(cash.get('collected_at') or '미확인'),
                     '적격성: C15 잠정 관측·전체 매매 적격 확정 아님',
                     'C16은 '+pool['generated_at']+' 후속 참고 분석. 선택 시점에 알았던 차트로 소급하지 않음.',
                     '일봉 구조형 가격 영역·OHLCV 추정 영역 / 실제 가격대별 거래량 아님']
            if code not in lookup:
                item.update(card_path=None,mini_chart_path=None,status='unavailable',reason='C16 해당 종목 저장 카드 미확인')
            else:
                card_source=top5_dashboard.local_asset(asset_root,item['card_path'])
                destination=output/code;destination.mkdir(exist_ok=True)
                if not (destination/'card.json').exists():shutil.copyfile(card_source,destination/'card.json')
                image=item.get('mini_chart_path')
                if image and top5_dashboard.local_asset(asset_root,image).is_file():
                    if not (destination/'mini.png').exists():shutil.copyfile(top5_dashboard.local_asset(asset_root,image),destination/'mini.png')
                    item['mini_chart_path']=code+'/mini.png'
                item['card_path']=code+'/card.json'
            item['summary']=summary;items.append(item)
    report=dict(schema_version='arvis-c12-chart-top5-v1',selection_source='C15',source_kind='readonly_adapter',
        environment='real',c12_as_of=snapshot['reference_at'],generated_at=pool['generated_at'],items=items,
        card_ui='position_only' if snapshot.get('display_ranking') else 'compact')
    turn=snapshot['diagnostics']['source_report']['analysis']['snapshot_selection']['selected']['0186']['snapshot']
    report['quote_at']=turn.get('request_started_at') if turn else None
    report['daily_dates']=sorted({json.loads((output/i['card_path']).read_text(encoding='utf8')).get('mini_chart_metadata',{}).get('latest_date')
        for i in items if i.get('card_path')} - {None})
    path=output/(slot.replace(':','')+'-cards.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return path


def build(snapshots,card_pool,*,daily_db,checkpoint,output):
    output=Path(output).absolute()
    if output.exists() or not output.resolve().is_relative_to(ROOT/'diagnostics'):raise ValueError('new_development_output_required')
    output.mkdir(parents=True)
    pool=json.loads(Path(card_pool).read_text(encoding='utf8'));asset_root=Path(card_pool).parent
    from .c16_display import grouped_snapshot
    views={s['reference_at']:grouped_snapshot(s) for s in snapshots}
    # Keep source artifacts intact; reuse them in this development bundle.
    for item in pool['items']:
        for key in ('card_path','mini_chart_path'):
            if item.get(key):
                target=output/item[key];target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(top5_dashboard.local_asset(asset_root,item[key]),target)
    asset_root=output
    required={leader['stock_code']:leader for view in views.values() for sector in view['analysis']['sectors'] for leader in sector['leaders']}
    missing={c:l for c,l in required.items() if c not in {i['stock_code'] for i in pool['items']}}
    if missing:
        from . import krx_c14
        from .analyst.mini_chart import chart_series,render_png
        from .c16_bridge import position
        from .daily_chart import fingerprint
        rows,index=krx_c14.read_inputs(list(missing),daily_db)
        for code,leader in missing.items():
            folder=output/code;folder.mkdir(exist_ok=True)
            card_result=krx_c14.make_card(rows[code],index,code=code,name=leader['name'],as_of=pool['generated_at'])
            card=card_result['card']
            try:
                series=chart_series(card,card_result['observed']);review=position(series,card_result['observed'])
                render_png(series,card,folder/'mini.png')
                card.update(position_review=review,mini_chart_metadata=series,mini_chart_path='mini.png',mini_chart_status='available' if series['candle_count']==60 else 'partial',
                    resistance_zone_low=series['resistance_zone']['low'],resistance_zone_high=series['resistance_zone']['high'],
                    resistance_zone_source=series['resistance_zone']['source'],resistance_zone_status=series['resistance_zone']['status'])
            except ValueError:card.update(position_review=dict(observation_type='데이터 부족'),mini_chart_path=None)
            card['card_reference']=fingerprint({k:v for k,v in card.items() if k!='card_reference'})
            (folder/'card.json').write_text(json.dumps(card,ensure_ascii=False,indent=2),encoding='utf8')
            pool['items'].append(dict(stock_code=code,card_path=code+'/card.json',mini_chart_path=code+'/mini.png' if card.get('mini_chart_path') else None))
    byslot={instant(s['reference_at']).strftime('%H:%M'):s for s in snapshots}
    latest=max(snapshots,key=lambda s:instant(s['reference_at']));raw=latest['diagnostics']['source_report']
    selected=raw['analysis']['snapshot_selection']['selected']['0198']['snapshot']
    original=cross_adapter.adapt(raw['paths']['ranking'],raw['paths']['turnover'],ranking_at=selected['collection_started_at'],environment='real')
    baseline=eligibility_dashboard.build(original);timeline=baseline['timeline']
    earliest=min(f['collected_at'] for f in timeline['frames'])
    official,_=c15_identity.load_official(daily_db,checkpoint,as_of=earliest)
    records=load(raw['paths']['theme'])['records']
    audit=[];new_frames=[];contexts={}
    original_runs=[*original['history'],original['ranking']]
    for frame in timeline['frames']:
        at=frame['collected_at'];slot=instant(at).strftime('%H:%M');snapshot=byslot.get(slot)
        current=next(r for r in original_runs if r['id']==frame['collection_id'])
        if snapshot:
            src=snapshot['diagnostics']['source_report'];rank=src['analysis']['snapshot_selection']['selected']['0198']['snapshot']
            if rank['id']!=frame['collection_id']:raise ValueError('C15_Radar_collection_mismatch')
            document=cross_adapter.adapt(src['paths']['ranking'],src['paths']['turnover'],ranking_at=current['collected_at'],environment='real')
        else:
            document=deepcopy(original);document['ranking']=current
            document['history']=[r for r in original_runs if instant(r['collected_at'])<instant(at)]
        document=mapped_document(document,records,official,at)
        bundle=eligibility_dashboard.build(document)
        mapped=next(f for f in bundle['timeline']['frames'] if f['collection_id']==frame['collection_id'])
        mapped['index']=frame['index'];new_frames.append(mapped)
        before=[r['stk_cd'] for r in current['rows'] if not original.get('sectors',{}).get(r['stk_cd'])]
        after=[r['stk_cd'] for r in current['rows'] if not document['sectors'].get(r['stk_cd'])]
        audit.append(dict(slot=slot,collection_id=frame['collection_id'],top20=[r['stk_cd'] for r in current['rows']],
            raw_tag_unclassified=len(before),C15_reference_unclassified=len(after),unclassified_codes=after,
            c15_snapshot_available=bool(snapshot)))
        contexts[frame['collection_id']]=(bundle,snapshot,slot)
    timeline=deepcopy(timeline);timeline['frames']=new_frames
    def render(frame):
        bundle,snapshot,slot=contexts[frame['collection_id']]
        # No future financial pair is exposed on slots without a C15 snapshot.
        html=eligibility_dashboard.frame_html(frame,bundle) if snapshot else bubble_timeline.frame_html(frame)
        if snapshot:
            headers=''.join('<li>'+str(s['rank'])+'위 '+escape(s['name'])+' · 점수 '+format(Decimal(s['score']),'.3f')+' · 상승 관측 '+str(s['rising_count'])+'종목</li>' for s in snapshot['analysis']['sectors'][:3])
            freshness=snapshot.get('turnover_freshness') or {}
            turn=snapshot['diagnostics']['source_report']['analysis']['snapshot_selection']['selected']['0186']['snapshot']
            note='0198 '+slot+' / 0186 '+(instant(turn['request_started_at']).strftime('%H:%M') if turn else '미확인')+(' (이전 자료 대체)' if freshness.get('is_fallback') else '')
            intro='<section class="c15-linked"><h2>C15 상승 주도섹터 TOP3 · '+slot+'</h2><ol>'+headers+'</ol><p>'+escape(note)+' · 잠정 관측, 확정 매매 신호 아님</p><p>Radar는 0198 TOP20 관심 분포, C15는 0198+0186 부분 관측의 상승 분석입니다. 순위를 혼동하지 않습니다.</p><a href="#c12-top5">대표 종목 9개 · C16 일봉 차트 보기</a></section>'
            view=views[snapshot['reference_at']]
            (output/(slot.replace(':','')+'-display-ranking.json')).write_text(json.dumps(view['display_ranking'],ensure_ascii=False,indent=2),encoding='utf8')
            index=cards_for_snapshot(view,pool,asset_root,output,slot)
            html=top5_dashboard.attach_top5(html,index,output/'dashboard.html',limit=9)
            html=html.replace('C12 snapshot:','C15 선정:')
        else:intro='<section class="c15-linked"><h2>C15 결과 미확인 · '+slot+'</h2><p>이 시각의 C15 선정 결과가 없어 다른 시점 TOP3나 차트로 대체하지 않습니다. Radar 테마 참조 매핑과 TOP20 관측은 유지합니다.</p></section>'
        html=html.replace('<main class="bubble-root">','<main class="bubble-root">'+intro,1)
        html=html.replace('</head>',"<style>.c15-linked{padding:14px;background:#fff;border:1px solid #cbd5e1;border-radius:8px;margin:10px 0}.c15-linked li{margin:6px 0}</style></head>",1)
        from .c16_ui import trader_frame
        result=trader_frame(html,frame,views[snapshot['reference_at']] if snapshot else None,slot)
        if snapshot:
            from .c16_calendar import capture_am_view
            capture_am_view(views[snapshot['reference_at']],frame=result,charts=json.loads(Path(index).read_text(encoding='utf8')),
                asset_root=output,source=str(output/(slot.replace(':','')+'-display-ranking.json')))
        return result
    html=bubble_timeline.render(timeline,frame_renderer=render)
    html=html.replace('sandbox="allow-same-origin"','sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"')
    html=html.replace("default-src 'none';","default-src 'none'; img-src 'self';",1)
    from .c16_sessions import session_dashboard
    report_roots=[ROOT/'reports']
    if raw.get('paths',{}).get('ledger'):report_roots.append(Path(raw['paths']['ledger']).parents[2])
    html=session_dashboard(html,day=latest['reference_at'][:10],reports_roots=report_roots,output=output,daily_db=daily_db)
    (output/'dashboard.html').write_text(html,encoding='utf8')
    (output/'connection-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    return audit


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,action='append',required=True);p.add_argument('--cards',type=Path,required=True)
    p.add_argument('--daily-db',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit=build([json.loads(path.read_text(encoding='utf8')) for path in a.snapshot],a.cards,
        daily_db=a.daily_db,checkpoint=a.checkpoint,output=a.output)
    print(json.dumps(dict(frames=len(audit),output=str(a.output),api_calls=0)))


if __name__=='__main__':main()
