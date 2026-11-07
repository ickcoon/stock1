"""Presentation adapter for saved C16 morning frames and existing PM reports."""
from decimal import Decimal, InvalidOperation
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3

from .am_time_policy import instant
from .c16_news import cached_summary
from .c16_ui import company_news, REPRESENTATIVE_CSS, NEWS_CSS, RANK_CSS


def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,TypeError):return {}


def replay_afternoon(root,day,slot,*,ranking_path=None,turnover_path=None,settings=None):
    """Reuse the original pair/eligibility/PM engines over archived DBs only."""
    from . import cross_adapter,eligibility_dashboard,session_review
    ranking=Path(ranking_path) if ranking_path else root.parent/'data/stock1-real.db'
    turnover=Path(turnover_path) if turnover_path else root.parent/'data/turnover'/day/(slot.replace(':','')+'.db')
    if not ranking.is_file() or not turnover.is_file():return None
    try:
        # The existing adapter holds read-only source handles and rejects active
        # WAL/journal files; do not snapshot, checkpoint, copy or create a DB.
        with cross_adapter.readonly_database(ranking) as (db,_):
            runs=[dict(row) for row in db.execute("SELECT id,collected_at,scheduled_at,record_count FROM collections WHERE environment='real' AND api_id='ka00198' AND status='success' AND scheduled_at=?",(day+'T'+slot+':00+09:00',))]
        if len(runs)!=1:return None
        pair=cross_adapter.adapt(ranking,turnover,ranking_at=runs[0]['collected_at'],environment='real')
        bundle=eligibility_dashboard.build(pair)
        # Same defaults as the original workflow when these optional evidence
        # files are absent; never mark observations verified to obtain candidates.
        settings=settings if settings is not None else read(root.parent/'workflow-config.json')
        caps=Path(settings['step6_capital_json']) if settings.get('step6_capital_json') else None
        proofs=read(settings['step6_proofs_json']) if settings.get('step6_proofs_json') else None
        if caps:
            records=json.loads(caps.read_text(encoding='utf-8-sig'))
            bundle['eligibility']=eligibility_dashboard.review(bundle['comparisons'][1000],records,at=runs[0]['collected_at'],proofs=proofs)
        review=bundle['eligibility']
        decision=session_review.afternoon_review(review,slot)
        from .c15_leadership import reference_afternoon
        reference=reference_afternoon(pair,as_of=runs[0]['collected_at'])
        stamps=set()
        for row in pair['ranking']['rows']:
            raw=json.loads(row['raw_json'])
            d,t=str(raw.get('dt','')),str(raw.get('tm',''))
            if re.fullmatch(r'\d{8}',d) and re.fullmatch(r'\d{6}',t):
                stamps.add(d[:4]+'-'+d[4:6]+'-'+d[6:]+' '+t[:2]+':'+t[2:4]+':'+t[4:])
        return dict(day=day,slot=slot,analysis_at=runs[0]['collected_at'],status='replayed',
            candidates=reference['candidates'],confirmed_candidates=decision['candidates'],reference=reference,
            root=root,folder=root/('c12x-'+day.replace('-','')+'-'+slot.replace(':','')),
            replay=dict(ranking_count=len(pair['ranking']['rows']),turnover_count=len(pair['turnover']['rows']),
                ranking_at=runs[0]['collected_at'],source_stamps=sorted(stamps),
                turnover_at=pair['turnover'].get('request_started_at'),turnover_status=pair['turnover'].get('status'),
                pagination_complete=bool(pair['turnover'].get('pagination_complete')),
                turnover_test_mode=bool(pair['turnover'].get('test_mode')),
                prefilter_count=sum(bool(row.get('filter_selected')) for row in review['rows']),
                reasons=sorted({reason for row in review['rows'] for reason in row.get('qualification',{}).get('reasons',[])})))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):return None


def afternoon_data(roots,day,slot):
    """Prefer saved selections, otherwise reuse existing analysis on saved data."""
    held={};ledger_path=None
    for root in map(Path,roots):
        ledger_path=root/'workflow'/day/(slot.replace(':','')+'.json')
        ledger=read(ledger_path)
        if ledger:held=ledger.get('review',{})
        folders=[root/('c12x-'+day.replace('-','')+'-'+slot.replace(':',''))]
        if held.get('report'):
            saved=Path(held['report']).resolve()
            if saved.is_relative_to(root.resolve()):folders.insert(0,saved)
        for folder in folders:
            report=read(folder/'telegram.json')
            try:
                at=instant(report['selection_cutoff_at'])
                valid=(report.get('selection_type')=='PM_CLOSING_REVIEW'
                    and report.get('source_kind')=='readonly_adapter' and report.get('environment')=='real'
                    and report.get('trade_date')==day and at.date().isoformat()==day and at.strftime('%H:%M')==slot
                    and isinstance(report.get('pm_candidates'),list))
            except (KeyError,ValueError,TypeError):valid=False
            if valid:
                details={r['stock_code']:r for r in read(folder/'step6d.json').get('rows',[]) if isinstance(r,dict) and r.get('stock_code')}
                observations={r['stock_code']:r for r in report.get('observation',{}).get('stocks',[]) if isinstance(r,dict) and r.get('stock_code')}
                candidates=[]
                for selected in report['pm_candidates']:
                    code=selected.get('stock_code')
                    if not isinstance(code,str) or not re.fullmatch(r'[0-9A-Z]{6}',code):continue
                    candidates.append({**observations.get(code,{}),**details.get(code,{}),**selected})
                return dict(day=day,slot=slot,analysis_at=at.isoformat(),candidates=candidates,
                            status='available',folder=folder,root=root)
        replay=replay_afternoon(root,day,slot)
        if replay is not None:return replay
    return dict(day=day,slot=slot,candidates=[],status='missing',reason=held.get('reason'))


def afternoon_day(roots,preferred):
    """Use the morning date if it has reports; otherwise a real saved PM date."""
    dates=set()
    for root in map(Path,roots):
        for path in root.glob('c12x-*-*/telegram.json'):
            report=read(path)
            if report.get('selection_type')=='PM_CLOSING_REVIEW' and report.get('source_kind')=='readonly_adapter':
                try:
                    at=instant(report['selection_cutoff_at'])
                    if at.strftime('%H:%M') in ('14:30','15:00'):dates.add(at.date().isoformat())
                except (KeyError,ValueError,TypeError):pass
    return preferred if preferred in dates or not dates else max(dates)


def pm_chart(data,code,output,daily_db=None):
    """Reuse a phase-matched saved chart, or the existing C16/C14 PNG builder."""
    from .top5_dashboard import local_asset
    root=data['root'].parent
    chart_folder=root/'diagnostics/daily_chart'/data['folder'].name
    index=read(chart_folder/'top5.json')
    try:matched=instant(index['c12_as_of'])==instant(data['analysis_at'])
    except (KeyError,ValueError,TypeError):matched=False
    if matched:
        for item in index.get('items',[]):
            if item.get('stock_code')==code and item.get('mini_chart_path'):
                try:
                    path=local_asset(chart_folder,item['mini_chart_path'])
                    if path.is_file():return os.path.relpath(path,output).replace('\\','/')
                except ValueError:pass
    if daily_db is None:return None
    # No candidates means this branch is never reached. Source DBs are read only.
    try:
        target=Path(output)/('pm-'+data['day'].replace('-','')+'-'+data['slot'].replace(':',''))/code
        png=target/'mini.png'
        if png.is_file():return os.path.relpath(png,output).replace('\\','/')
        os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parents[1]/'.venv/mplconfig'))
        from . import krx_c14
        from .analyst.mini_chart import chart_series,render_png
        from .c16_bridge import position
        if not png.is_file():
            rows,index=krx_c14.read_inputs([code],daily_db)
            stock=next(r for r in data['candidates'] if r['stock_code']==code)
            result=krx_c14.make_card(rows[code],index,code=code,name=stock.get('stock_name',code),as_of=data['analysis_at'])
            series=chart_series(result['card'],result['observed']);position(series,result['observed'])
            target.mkdir(parents=True,exist_ok=True);render_png(series,result['card'],png)
        return os.path.relpath(png,output).replace('\\','/')
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):return None


def pm_frame(data,output,daily_db=None):
    from .top5_dashboard import CSS as CHART_CSS,COMPACT_CSS
    from .c15_briefing_data import format_capital
    def number(value,divisor=1,suffix='',signed=False,digits=2):
        try:
            value=Decimal(str(value))/Decimal(divisor)
            return (format(value,('+' if signed else '')+',.'+str(digits)+'f')+suffix) if value.is_finite() else '미확인'
        except (InvalidOperation,TypeError,ValueError):return '미확인'
    heading='<h2>오후 종가매매 · '+escape(data['day'])+' '+data['slot']+'</h2>'
    if data['status']=='missing':
        reason='시장 기준시각·거래일 미확인으로 기존 분석 보류' if data.get('reason')=='source_market_time_unverified' else '해당 시각의 저장된 후보 분석 결과가 없습니다.'
        body=heading+'<p>분석 데이터 없음</p><p>'+reason+'</p>'
    else:
        cards=[]
        for row in data['candidates']:
            code=row['stock_code'];name=row.get('stock_name') or row.get('name') or code
            change=row.get('change_percent_raw',row.get('change_percent'))
            cash=row.get('turnover_raw',row.get('turnover_million_krw'))
            capital=row.get('market_cap_won_verified',row.get('market_cap_won_reference'))
            sectors=row.get('sectors',[])
            news=(dict(companies=data['frozen_news']) if 'frozen_news' in data else
                  cached_summary(' / '.join(sectors),[dict(stock_code=code,name=name)],data['analysis_at']))
            image=row.get('chart_path') if data.get('frozen_snapshot') else pm_chart(data,code,output,daily_db)
            chart='<a target="_blank" rel="noopener" href="'+escape(image,quote=True)+'"><img style="width:100%" src="'+escape(image,quote=True)+'" alt="'+escape(name,quote=True)+' 미니차트"></a>' if image else '<p>미니차트 데이터 없음</p>'
            ordinal=len(cards)+1
            cards.append('<article class="top5-card" data-sector-rank="'+str(row.get('sector_rank',1))+'"><h3 class="representative-name">'+str(ordinal)+'. '+escape(name)+'</h3><p>'+number(change,suffix='%',signed=True)+' | 거래대금 '+number(cash,100,'억',digits=0)+' | '+escape(format_capital(capital))+'</p><p>'+escape(' / '.join(sectors) or '소속 섹터 미확인')+'</p><ul style="padding:0">'+company_news(news.get('companies',{}).get(code))+'</ul>'+chart+'</article>')
        context=''
        if data.get('replay'):
            observed=data['replay']
            context='<p>참고 후보 '+str(len(data['candidates']))+'개 · 최종 적격 후보 '+str(len(data.get('confirmed_candidates',[])))+'개 · 과거 시총 참고값</p>'
            if not cards:context+='<p>검증 보류 · 최종 후보 없음</p>'
        elif data.get('reference_only') or any(row.get('reference_only') for row in data['candidates']):
            context='<p>참고 후보 · 최종 매매 적격성 미확정 · 과거 시총 참고값</p>'
        body=heading+context+('<section id="c12-top5" class="top5-compact"><div class="top5-grid">'+''.join(cards)+'</div></section>' if cards else '<p>기존 분석 결과: 후보 없음</p>')
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>body{font-family:system-ui;background:#f5f7fa;color:#243447;padding:14px}.top5-card{background:white;border:1px solid #dbe3eb;border-radius:8px;padding:12px;margin:10px 0}'+CHART_CSS+COMPACT_CSS+REPRESENTATIVE_CSS+NEWS_CSS+RANK_CSS+'</style></head><body>'+body+'</body></html>'


def session_dashboard(html,*,day,reports_roots,output,daily_db=None,snapshot_root=None):
    """Native radio tabs; no browser scripts, collection or candidate generation."""
    class Frames(HTMLParser):
        def __init__(self):super().__init__();self.frames={}
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='iframe' and attrs.get('srcdoc'):
                match=re.search(r'\b(09:(?:40|45|50))(?:[:\s]|$)',attrs.get('title',''))
                if match:self.frames[match.group(1)]=attrs['srcdoc']
    parsed=Frames();parsed.feed(html)
    pm_day=afternoon_day(reports_roots,day)
    styles='''body{margin:0;background:#f5f7fa;color:#243447;font-family:system-ui}.session-root{padding:12px 18px}.session-root h1{font-size:21px;margin:5px 0 12px}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.tab-controls{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap}.tab-controls label{padding:8px 18px;background:white;border:1px solid #cbd5e1;border-radius:7px;cursor:pointer}.session-panel,.session-frame{display:none}.session-frame{width:100%;height:calc(100vh - 190px);min-height:650px;border:0}.tab-controls:has(#session-am:checked) label[for=session-am],.tab-controls:has(#session-pm:checked) label[for=session-pm]{background:#26394d;color:white}.session-root:has(#session-am:checked) #panel-am,.session-root:has(#session-pm:checked) #panel-pm{display:block}input:focus-visible+label{outline:2px solid #64748b}'''
    panels=[]
    for key,title,slots,default in [('am','오전 주도주',('09:40','09:45','09:50'),'09:50'),('pm','오후 종가매매',('14:30','15:00'),'15:00')]:
        parts=['<section class="session-panel" id="panel-'+key+'"><p>'+escape(day if key=='am' else pm_day)+'</p><div class="tab-controls">']
        frames=[]
        for slot in slots:
            ident=key+'-'+slot.replace(':','')
            parts.append('<input class="sr-only" type="radio" name="'+key+'-time" id="'+ident+'"'+(' checked' if slot==default else '')+'><label for="'+ident+'">'+slot+'</label>')
            frame=parsed.frames.get(slot,'<p>분석 데이터 없음</p>') if key=='am' else pm_frame(afternoon_data(reports_roots,pm_day,slot),output,daily_db)
            frames.append('<iframe class="session-frame" id="frame-'+ident+'" title="'+title+' '+slot+'" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" srcdoc="'+escape(frame,quote=True)+'"></iframe>')
            styles+='.session-root:has(#'+ident+':checked) #frame-'+ident+'{display:block}.tab-controls:has(#'+ident+':checked) label[for='+ident+']{background:#26394d;color:white}'
        panels.append(''.join(parts)+'</div>'+''.join(frames)+'</section>')
    csp="default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; frame-src 'self' about:; base-uri 'none'; form-action 'none'"
    result='<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+csp+'"><title>ARVIS C16 · 오전/오후 분석</title><style>'+styles+'</style></head><body><main class="session-root"><h1>ARVIS C16</h1><div class="tab-controls"><input class="sr-only" type="radio" name="session" id="session-am" checked><label for="session-am">오전 주도주</label><input class="sr-only" type="radio" name="session" id="session-pm"><label for="session-pm">오후 종가매매</label></div>'+''.join(panels)+'</main></body></html>'
    from .c16_calendar import attach_calendar,STORE
    return attach_calendar(result,root=snapshot_root if snapshot_root is not None else STORE,output=output)
