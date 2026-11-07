"""Offline 60-session mini PNG from C14 validated chart bars and card values."""
from copy import deepcopy
from datetime import date
from decimal import Decimal,localcontext
from pathlib import Path
import argparse
import json
from .chart import mean_close,number
from ..daily_chart import fingerprint
from ..leader_tracking import timestamp


RESISTANCE_ZONE_HALF_WIDTH_PERCENT = Decimal('0.75')
RESISTANCE_ZONE_ALPHA = 0.22
MA_COLORS = {'MA5':'#d62728','MA20':'#c46a00','MA60':'#1f6b3a'}

STRUCTURAL_MIN_BULL_BODY_PERCENT = Decimal('3')
STRUCTURAL_MIN_BULL_BODY_RANGE_RATIO = Decimal('0.60')
STRUCTURAL_MIN_BEAR_UPPER_RANGE_RATIO = Decimal('0.30')
STRUCTURAL_MIN_UPPER_BODY_RATIO = Decimal('0.50')
STRUCTURAL_MAX_ZONE_WIDTH_PERCENT = Decimal('5')
STRUCTURAL_MAX_OBSERVED_GAP_DAYS = 7


def structural_resistance(bars,close):
    """Recent consecutive observed bars only; an unvalidated trading hypothesis."""
    for bull,bear in reversed(list(zip(bars,bars[1:]))):
        gap=(date.fromisoformat(bear['trade_date'])-date.fromisoformat(bull['trade_date'])).days
        if not 0<gap<=STRUCTURAL_MAX_OBSERVED_GAP_DAYS:continue
        body=bull['close']-bull['open'];span=bull['high']-bull['low']
        bear_body=bear['open']-bear['close'];bear_span=bear['high']-bear['low']
        upper=bear['high']-bear['open']
        low,high=bull['close'],bear['high']
        if (body<=0 or span<=0 or bear_body<=0 or bear_span<=0 or high<=low or low<=close):continue
        if (body/bull['open']*100<STRUCTURAL_MIN_BULL_BODY_PERCENT
            or body/span<STRUCTURAL_MIN_BULL_BODY_RANGE_RATIO
            or upper/bear_span<STRUCTURAL_MIN_BEAR_UPPER_RANGE_RATIO
            or upper/bear_body<STRUCTURAL_MIN_UPPER_BODY_RATIO
            or (high/low-1)*100>STRUCTURAL_MAX_ZONE_WIDTH_PERCENT):continue
        return dict(status='provisional',source='bull_close_next_observed_bear_upper_wick',
            low=str(low),high=str(high),reason=None,pattern_verified=False,
            bull={k:str(bull[k]) for k in ('trade_date','open','high','low','close')},
            bear={k:str(bear[k]) for k in ('trade_date','open','high','low','close')},
            thresholds=dict(bull_body_percent=str(STRUCTURAL_MIN_BULL_BODY_PERCENT),
                bull_body_range_ratio=str(STRUCTURAL_MIN_BULL_BODY_RANGE_RATIO),
                bear_upper_range_ratio=str(STRUCTURAL_MIN_BEAR_UPPER_RANGE_RATIO),
                upper_body_ratio=str(STRUCTURAL_MIN_UPPER_BODY_RATIO),
                max_zone_width_percent=str(STRUCTURAL_MAX_ZONE_WIDTH_PERCENT),
                max_observed_gap_days=STRUCTURAL_MAX_OBSERVED_GAP_DAYS),
            selection='most_recent_above_latest_close',
            calendar_status='consecutive_observed_rows_not_independently_verified_sessions',
            order_signal=False,score_applied=False)
    return None


def chart_series(card,observed):
    if card.get('schema_version')!='arvis-chart-card-v1' or observed.get('schema_version')!='arvis-observed-daily-v1':
        raise ValueError('mini_chart_schema_required')
    if any(card.get(k)!=observed.get(k) for k in ('stock_code','environment','market','data_origin','as_of')):
        raise ValueError('mini_chart_scope_mismatch')
    bars=[]
    for raw in observed.get('chart_bars',[]):
        day=date.fromisoformat(raw['trade_date'])
        if day>=timestamp(card['as_of']).date():raise ValueError('mini_chart_future_bar')
        values={k:number(raw.get(k),volume=k=='volume') for k in ('open','high','low','close','volume')}
        if any(v is None for v in values.values()) or not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:
            raise ValueError('mini_chart_invalid_ohlcv')
        bars.append(dict(values,trade_date=day.isoformat()))
    bars.sort(key=lambda b:b['trade_date'])
    if not bars:raise ValueError('mini_chart_no_daily_bars')
    if len({b['trade_date'] for b in bars})!=len(bars):raise ValueError('mini_chart_duplicate_date')
    # Never silently bridge a held/missing source row.
    held=observed.get('held_dates',[])
    if any(d is None or d>=bars[0]['trade_date'] for d in held):raise ValueError('mini_chart_held_bar_in_window')
    bars=bars[-119:];start=max(0,len(bars)-60);visible=bars[start:]
    quote=observed.get('reference_close') or {}
    if quote.get('trade_date')!=visible[-1]['trade_date'] or number(quote.get('value'))!=visible[-1]['close']:
        raise ValueError('mini_chart_close_mismatch')
    stored=(card.get('provenance',{}).get('observed_daily') or {})
    if stored and (stored.get('page_fingerprints')!=observed.get('page_fingerprints')
                   or stored.get('adjustment_request')!=observed.get('adjustment_request')):
        raise ValueError('mini_chart_source_mismatch')
    averages={}
    with localcontext() as precision:
        precision.prec=50
        for n in (5,20,60):
            averages[f'MA{n}']=[str(v) if (v:=mean_close(bars[:i+1],n)) is not None else None for i in range(start,len(bars))]
            existing=card.get('moving_averages',{}).get(f'MA{n}',{})
            if existing.get('status') in ('verified','provisional') and existing.get('value') is not None and averages[f'MA{n}'][-1] is not None:
                if Decimal(existing['value'])!=Decimal(averages[f'MA{n}'][-1]):raise ValueError('mini_chart_ma_mismatch')
    areas=(card.get('resistance',{}).get('analysis') or {}).get('areas',[])
    close=visible[-1]['close'];overhead=[number(a.get('typical_price')) for a in areas]
    overhead=[v for v in overhead if v is not None and v>close]
    line=min(overhead) if overhead else max(b['high'] for b in visible)
    zone=dict(status='unavailable',low=None,high=None,source=None,reason='resistance_estimate_missing')
    if overhead and card.get('resistance',{}).get('status') in ('verified','provisional'):
        with localcontext() as precision:
            precision.prec=50
            fraction=RESISTANCE_ZONE_HALF_WIDTH_PERCENT/100
            zone=dict(status=card['resistance']['status'],low=str(line*(1-fraction)),high=str(line*(1+fraction)),
                center=str(line),half_width_percent=str(RESISTANCE_ZONE_HALF_WIDTH_PERCENT),
                source='c14_20bar_volume_proxy_typical_price',reason=None)
    calculated_zone=deepcopy(zone)
    structural=structural_resistance(visible,close)
    if structural is not None:zone=structural
    return dict(resistance_zone=zone,calculated_resistance_zone=calculated_zone,structural_resistance_candidate=structural,bars=[{k:str(v) for k,v in b.items()} for b in visible],moving_averages=averages,
        candle_count=len(visible),input_bars=len(bars),latest_close=str(close),latest_date=visible[-1]['trade_date'],
        horizontal_line=dict(value=str(line),kind='potential_resistance' if overhead else 'recent_60_bar_high'),
        data_origin=observed['data_origin'],analysis_status=observed['status'],
        adjustment_request=observed.get('adjustment_request'),price_basis_verified=observed.get('price_basis_verified',False))


def render_png(series,card,path):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import StrMethodFormatter,MaxNLocator
    from matplotlib import rc_context,font_manager
    font=Path('C:/Windows/Fonts/malgun.ttf')
    family='DejaVu Sans'
    if font.is_file():
        font_manager.fontManager.addfont(str(font));family=font_manager.FontProperties(fname=str(font)).get_name()
    with rc_context({'font.family':family,'font.size':10,'axes.unicode_minus':False}):
        fig=Figure(figsize=(9.6,6),dpi=100,layout='constrained');FigureCanvasAgg(fig)
        try:
            price,volume=fig.subplots(2,1,sharex=True,gridspec_kw={'height_ratios':[3.5,1]})
            bars=series['bars']
            for i,bar in enumerate(bars):
                o,h,l,c,v=(float(bar[k]) for k in ('open','high','low','close','volume'))
                color='#d83a45' if c>=o else '#2463c4'
                price.vlines(i,l,h,color=color,linewidth=1)
                price.add_patch(Rectangle((i-.3,min(o,c)),.6,abs(c-o),facecolor=color,edgecolor=color))
                if o==c:price.hlines(c,i-.3,i+.3,color=color,linewidth=1.4)
                volume.bar(i,v/1000000,width=.65,color=color)
            for key,color in MA_COLORS.items():
                price.plot(range(len(bars)),[float(v) if v is not None else float('nan') for v in series['moving_averages'][key]],label=key,color=color,linewidth=1.5)
            line=series['horizontal_line'];value=float(line['value'])
            zone=series['resistance_zone']
            if zone['status']!='unavailable':
                low,high=float(zone['low']),float(zone['high'])
                price.axhspan(low,high,color='#f4b942',alpha=RESISTANCE_ZONE_ALPHA,zorder=0,label='Structural resistance candidate' if zone['source']=='bull_close_next_observed_bear_upper_wick' else f'Calculated zone (+/-{RESISTANCE_ZONE_HALF_WIDTH_PERCENT}%)')
                for bound,label in ((high,'Upper'),(low,'Lower')):
                    price.annotate(f'{label} {bound:,.0f}',xy=(.995,bound),xycoords=('axes fraction','data'),
                        ha='right',va='center',fontsize=8,color='#805b10',
                        bbox=dict(facecolor='white',edgecolor='none',alpha=.8,pad=1.5))
            else:
                price.axhline(value,color='#6b7280',linestyle='--',linewidth=.7,alpha=.4,label=f'Recent high {value:,.0f}')
            support=series.get('support_zone')
            nearest=series.get('nearest_resistance_candidate')
            if nearest:
                price.axhline(float(nearest['value']),color='#9866c5',linestyle=':',linewidth=1,label='Nearest overhead candidate '+format(float(nearest['value']),',.0f'))
            if support:
                price.axhspan(float(support['low']),float(support['high']),color='#23a889',alpha=.18,zorder=0,label='Daily pivot support estimate')
                price.annotate('Support '+format(float(support['low']),',.0f'),xy=(.995,float(support['low'])),xycoords=('axes fraction','data'),ha='right',va='top',fontsize=8,color='#14765e')
            if zone.get('bull'):
                price.text(.99,.98,zone['bull']['trade_date']+' bull close / '+zone['bear']['trade_date']+' bear wick',transform=price.transAxes,ha='right',va='top',fontsize=8)
            if support:
                price.text(.99,.92,'Support pivot '+support['trade_date'],transform=price.transAxes,ha='right',va='top',fontsize=8,color='#14765e')
            price.scatter([len(bars)-1],[float(series['latest_close'])],color='#111827',s=18,zorder=5)
            peak=max(range(len(bars)),key=lambda i:float(bars[i]['high']))
            price.scatter([peak],[float(bars[peak]['high'])],color='#6b7280',s=14,zorder=5)
            price.annotate('60-bar high '+format(float(bars[peak]['high']),',.0f')+' / '+bars[peak]['trade_date'],
                xy=(peak,float(bars[peak]['high'])),xytext=(0,7),textcoords='offset points',fontsize=7,color='#4b5563')
            name=card.get('stock_name') or ''
            zone_title='Structural candidate' if zone.get('source')=='bull_close_next_observed_bear_upper_wick' else 'Calculated zone' if zone['status']!='unavailable' else 'Recent high'
            price.set_title(f"{card['stock_code']} {name} | {series['latest_date']} | Close {float(series['latest_close']):,.0f} KRW | {zone_title}",loc='left',fontsize=11,pad=12)
            price.text(.01,.02,f"{series['analysis_status']} | adjustment request={series['adjustment_request']} | saved data, not live; OHLCV zones, NOT volume-at-price",transform=price.transAxes,fontsize=8,color='#6b7280')
            price.legend(loc='upper left',fontsize=8,ncol=2,frameon=False)
            price.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'));price.set_ylabel('KRW')
            volume.set_ylabel('Vol (M)');volume.yaxis.set_major_locator(MaxNLocator(3))
            ticks=sorted(set([0,*range(9,len(bars),10),len(bars)-1]))
            volume.set_xticks(ticks,[bars[i]['trade_date'][5:] for i in ticks]);volume.set_xlabel('Trading sessions')
            price.tick_params(labelbottom=False)
            for ax in (price,volume):
                ax.grid(axis='y',alpha=.15);ax.spines[['top','right']].set_visible(False);ax.set_xlim(-1,len(bars)+9 if zone['status']!='unavailable' else len(bars))
            with Path(path).open('xb') as target:fig.savefig(target,format='png')
        finally:fig.clear()


def with_mini_chart(card,observed,png_path):
    """Rendering is optional: any failure returns the intact analysis plus status."""
    result=deepcopy(card)
    try:
        series=chart_series(card,observed);render_png(series,card,png_path)
        result.update(mini_chart_status='available' if series['candle_count']==60 else 'partial',
            mini_chart_path=Path(png_path).name,mini_chart_metadata=series,
            resistance_zone_low=series['resistance_zone']['low'],resistance_zone_high=series['resistance_zone']['high'],
            resistance_zone_source=series['resistance_zone']['source'],resistance_zone_status=series['resistance_zone']['status'])
    except Exception:
        result.update(mini_chart_status='unavailable',mini_chart_path=None,
            mini_chart_metadata=dict(reason='render_failed_or_daily_data_invalid'),
            resistance_zone_low=None,resistance_zone_high=None,resistance_zone_source=None,resistance_zone_status='unavailable')
    result.pop('card_reference',None);result['card_reference']=fingerprint(result)
    return result


def main(argv=None):
    from .saved import read_json,diagnostic_pages
    from .observed_daily import analyze_observed_daily
    from ..daily_diagnostic import output_root
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--card-json',type=Path,required=True);p.add_argument('--diagnostic-json',type=Path,action='append',required=True)
    p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args(argv)
    try:
        card,_=read_json(args.card_json);pages=[];origin=None
        for path in args.diagnostic_json:
            part,kind=diagnostic_pages(read_json(path)[0])
            if origin is not None and kind!=origin:raise ValueError('mixed_origins')
            pages.extend(part);origin=kind
        observed=analyze_observed_daily(pages,as_of=card['as_of'],data_origin=origin)
        folder=output_root(args.output_dir)
        folder.mkdir(parents=True,exist_ok=False)
        result=with_mini_chart(card,observed,folder/f"{card['stock_code']}-mini.png")
        with (folder/'card.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    except (OSError,ValueError,TypeError,KeyError):p.error('Invalid saved C14 input or new diagnostics output required; no API calls')
    print(json.dumps(dict(mini_chart_status=result['mini_chart_status'],output_dir=str(folder),api_calls=0)))
    return 0


if __name__=='__main__':raise SystemExit(main())
