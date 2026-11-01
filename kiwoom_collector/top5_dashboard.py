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


def local_asset(base,value):
    if not isinstance(value,str):raise ValueError('local_path_required')
    path=Path(value)
    if path.is_absolute() or '..' in path.parts:raise ValueError('unsafe_asset_path')
    path=(base/path).resolve()
    if not path.is_relative_to(base.resolve()):raise ValueError('asset_outside_bundle')
    return path


def top5_section(top5_json,output_html):
    source=Path(top5_json).resolve();report,_=read_json(source)
    if report.get('schema_version')!='arvis-c12-chart-top5-v1' or report.get('source_kind') not in ('fixture','readonly_adapter'):
        raise ValueError('saved_top5_schema_required')
    demo=report['source_kind']=='fixture';output=Path(output_html).resolve()
    heading='\uc8fc\ub3c4\uc8fc Top5 \ucc28\ud2b8 \ubd84\uc11d'
    parts=[f'<section id="c12-top5"><h2>{heading}</h2>']
    if demo:parts.append('<p class="top5-demo">DEMO / SYNTHETIC — test data, not actual market leaders or recommendations.</p>')
    parts.append('<p>C12 snapshot: '+escape(str(report.get('c12_as_of')))+' | Generated: '+escape(str(report.get('generated_at')))+'. Existing sector / leader order. Saved chart data, not live quotes.</p><div class="top5-grid">')
    for item in report.get('items',[])[:5]:
        code=item.get('stock_code');name=item.get('stock_name') or code
        parts.append(f'<article class="top5-card" data-code="{escape(str(code),quote=True)}"><h3>#{escape(str(item.get("rank")))} {escape(str(name))} <small>{escape(str(code))}</small></h3>')
        evidence=item.get('c12_evidence') or {}
        parts.append('<p>C12: sector rank '+escape(str(item.get('c12_sector_rank')))+' / leader '+escape(str(item.get('c12_leader_rank')))+' | latest rank '+escape(str(item.get('c12_latest_rank')))+' | Top5 appearances '+escape(str(evidence.get('top5_appearances','unavailable')) )+'</p>')
        card=None
        try:
            if not isinstance(code,str):raise ValueError('string_code_required')
            card,_=read_json(local_asset(source.parent,item.get('card_path')))
            if (card.get('schema_version')!='arvis-chart-card-v1' or card.get('stock_code')!=code
                or card.get('environment')!=report['environment'] or card.get('data_origin')!=('synthetic_fixture' if demo else 'api_response')):raise ValueError('card_scope')
            if card.get('card_reference')!=fingerprint({k:v for k,v in card.items() if k!='card_reference'}):raise ValueError('card_fingerprint')
        except (OSError,ValueError,TypeError,KeyError):
            card=None
        if card is None:
            parts.append('<p class="top5-state">Analysis unavailable / failed: '+escape(str(item.get('reason') or 'card_missing_or_invalid'))+'</p>')
        else:
            image=None
            try:
                image=local_asset(source.parent,item.get('mini_chart_path'))
                if image.suffix.lower()!='.png' or not image.is_file():image=None
            except (ValueError,TypeError):pass
            if image:
                url=escape(quote(os.path.relpath(image,output.parent).replace('\\','/'),safe='/'),quote=True)
                parts.append(f'<a href="{url}" target="_blank" rel="noopener"><img src="{url}" alt="{escape(str(name),quote=True)} 60-session chart; click for original"></a>')
            else:parts.append('<p class="top5-state">Chart not generated / PNG missing</p>')
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
            parts.append('<ul>'+''.join('<li>'+escape(re.sub(r'-?\d+\.\d{3,}',lambda m:format(Decimal(m.group()),'.2f'),str(line)))+'</li>' for line in item.get('summary',[]))+'</ul>')
        parts.append('</article>')
    if not report.get('items'):parts.append('<p>No eligible C12 candidates available.</p>')
    parts.append('</div><p>No automated orders or confirmed buy signals. Provisional values remain unverified.</p></section>')
    return '<style>'+CSS+'</style>'+''.join(parts)


def attach_top5(html,top5_json,output_html):
    section=top5_section(top5_json,output_html)
    # Existing offline CSP blocks images; allow only same-origin saved assets.
    if "default-src 'none';" in html and 'img-src ' not in html:
        html=html.replace("default-src 'none';","default-src 'none'; img-src 'self';",1)
    marker='</main>' if '</main>' in html else '</body>'
    return html.replace(marker,section+marker,1)
