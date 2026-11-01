"""Read-only C12 published leaders -> saved C14 cards/mini PNG. No selection rules."""
import argparse
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import shutil
from . import am_briefing
from .am_candidate_adapter import normalize_code
from .client import KST
from .daily_chart import fingerprint
from .daily_diagnostic import output_root
from .leader_tracking import timestamp
from .analyst.saved import read_json,diagnostic_pages
from .analyst.card import chart_card
from .analyst.mini_chart import with_mini_chart
from .analyst.observed_daily import analyze_observed_daily


def published_top(report,limit=5):
    if type(limit) is not int or not 1<=limit<=5:raise ValueError('top_limit_1_to_5')
    am_briefing.validate(report)
    filters=report.get('candidate_filter',{})
    if filters.get('version')!='c12-am-candidate-join-v1':raise ValueError('existing_c12_product_filter_required')
    result=[];seen=set()
    for sector in report['sectors']:
        for local_rank,leader in enumerate(sector['leaders'],1):
            code=leader['stock_code']
            if not isinstance(code,str) or normalize_code(code)!=code:raise ValueError('exact_string_code_required')
            if filters['decisions'].get(code,{}).get('state')!='analysis_candidate' or leader['status']!='priority':continue
            if code in seen:continue
            seen.add(code)
            result.append(dict(rank=len(result)+1,stock_code=code,stock_name=leader.get('name'),
                c12_sector_rank=sector['rank'],c12_leader_rank=local_rank,c12_latest_rank=leader['latest_rank'],
                c12_evidence=deepcopy(leader),c12_sector_evidence=deepcopy(sector['evidence']),
                c12_eligibility=deepcopy(filters['decisions'][code])))
    return result[:limit]


def describe(card):
    lines=[]
    for key in ('MA5','MA20','MA60'):
        value=card.get('moving_averages',{}).get(key,{})
        if value.get('position'):lines.append(f"Close vs {key}: {value['position']}; gap {value.get('gap_percent')}% ({value['status']})")
    trend=card.get('daily_trend',{})
    if trend.get('ma20_above_ma60') is not None:lines.append('MA20 above MA60: '+str(trend['ma20_above_ma60']))
    high=card.get('high52',{})
    if high.get('price_vs_high_percent') is not None:lines.append(f"52-week high distance: {high['price_vs_high_percent']}% ({high['status']})")
    if card.get('resistance_zone_low') is not None:
        lines.append(f"Resistance zone {card['resistance_zone_low']}..{card['resistance_zone_high']} ({card['resistance_zone_source']}, {card['resistance_zone_status']})")
    if any(v.get('status')=='provisional' for v in card.get('moving_averages',{}).values()) or high.get('status')=='provisional':
        lines.append('Provisional analysis; not a confirmed buy signal')
    return lines or ['Chart data unavailable']


def integrate(report,artifacts,output_dir,*,limit=5,generated_at=None):
    selected=published_top(report,limit);folder=output_root(Path(output_dir))
    folder.mkdir(parents=True,exist_ok=False)
    generated=generated_at or datetime.now(KST).isoformat();timestamp(generated)
    result=dict(schema_version='arvis-c12-chart-top5-v1',generated_at=generated,
        c12_as_of=report['selection_cutoff_at'],source_kind=report['source_kind'],environment=report['environment'],
        market=report['market'],ordering='existing_sector_rank_then_published_leader_order',
        source_reference=report['evaluation_reference'],requested_limit=limit,items=[],
        candidate_policy_changed=False,order_signal=False,api_calls=0)
    for candidate in selected:
        code=candidate['stock_code'];item=deepcopy(candidate)
        item.update(status='unavailable',reason='chart_artifact_missing',card_path=None,mini_chart_path=None,mini_chart_status='unavailable',summary=[])
        artifact=artifacts.get(code)
        if not artifact:result['items'].append(item);continue
        try:
            card_source=None
            if artifact.get('card_json'):
                card_source=Path(artifact['card_json']);card,_=read_json(card_source)
            else:
                raw,_=read_json(artifact['report_json'])
                observed=read_json(artifact['observed_json'])[0] if artifact.get('observed_json') else None
                card=chart_card(raw,stock_name=candidate['stock_name'],observed_daily=observed)
            unsigned={k:v for k,v in card.items() if k!='card_reference'}
            if card.get('card_reference')!=fingerprint(unsigned):raise ValueError('card_fingerprint_mismatch')
            if (card.get('schema_version')!='arvis-chart-card-v1' or card.get('stock_code')!=code
                or card.get('environment')!=report['environment'] or card.get('market')!=report['market']
                or card.get('data_origin')!=('synthetic_fixture' if report['source_kind']=='fixture' else 'api_response')):
                raise ValueError('card_scope_mismatch')
            if timestamp(card['as_of'])>timestamp(generated):raise ValueError('future_card')
            target=folder/code;target.mkdir()
            if artifact.get('diagnostic_json'):
                pages=[];origin=None
                for path in artifact['diagnostic_json']:
                    part,kind=diagnostic_pages(read_json(path)[0])
                    if origin is not None and origin!=kind:raise ValueError('mixed_daily_origins')
                    pages.extend(part);origin=kind
                observed=analyze_observed_daily(pages,as_of=card['as_of'],data_origin=origin)
                card=with_mini_chart(card,observed,target/'mini.png')
            elif card_source and card.get('mini_chart_path'):
                path=Path(card['mini_chart_path'])
                if path.is_absolute() or '..' in path.parts:raise ValueError('nonlocal_mini_chart_path')
                source=card_source.parent/path
                try:
                    with source.open('rb') as f:
                        if f.read(8)!=b'\x89PNG\r\n\x1a\n':raise OSError('invalid PNG')
                    shutil.copyfile(source,target/'mini.png');card['mini_chart_path']='mini.png'
                except OSError:
                    card.update(mini_chart_status='unavailable',mini_chart_path=None)
            image=card.get('mini_chart_path')
            image_ok=bool(image and (target/image).is_file())
            if not image_ok:card.update(mini_chart_status='unavailable',mini_chart_path=None)
            card.pop('card_reference',None);card['card_reference']=fingerprint(card)
            with (target/'card.json').open('x',encoding='utf-8') as f:json.dump(card,f,ensure_ascii=False,indent=2,allow_nan=False)
            quote=(card.get('provenance',{}).get('observed_daily') or {}).get('reference_close') or card.get('provenance',{}).get('daily_reference_close')
            data_ok=any(v.get('status') in ('verified','provisional') for v in card.get('moving_averages',{}).values()) or card.get('high52',{}).get('status') in ('verified','provisional')
            item.update(status='available' if data_ok and image_ok else 'partial' if data_ok else 'unavailable',
                reason=None if data_ok and image_ok else 'mini_chart_missing_or_failed' if data_ok else 'chart_data_insufficient',
                card_path=f'{code}/card.json',mini_chart_path=f'{code}/'+image if image_ok else None,
                mini_chart_status=card['mini_chart_status'],analysis_as_of=card['as_of'],latest_close=deepcopy(quote),
                c12_chart_time_relation='historical_snapshot_comparison_not_point_in_time_selection',
                summary=describe(card))
        except Exception as exc:
            # Per-stock failures never stop the remaining C12 published leaders.
            allowed={'card_scope_mismatch','card_fingerprint_mismatch','future_card','nonlocal_mini_chart_path'}
            item.update(status='failed',reason=str(exc) if type(exc) is ValueError and str(exc) in allowed else 'invalid_or_unreadable_chart_artifact')
        result['items'].append(item)
    result['counts']={s:sum(i['status']==s for i in result['items']) for s in ('available','partial','unavailable','failed')}
    with (folder/'top5.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--c12-json',type=Path,required=True);p.add_argument('--artifacts-json',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--limit',type=int,default=5)
    a=p.parse_args(argv)
    try:
        report=read_json(a.c12_json)[0];manifest=read_json(a.artifacts_json)[0]
        result=integrate(report,manifest['stocks'],a.output_dir,limit=a.limit)
    except (OSError,ValueError,TypeError,KeyError):p.error('Invalid saved C12 input, artifacts manifest or new diagnostics output')
    print(json.dumps(dict(output=str(a.output_dir/'top5.json'),counts=result['counts'],api_calls=0)))
    return 0


if __name__=='__main__':raise SystemExit(main())
