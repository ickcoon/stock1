"""Small, append-only JSON selection snapshots and an offline calendar view."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .am_time_policy import instant

PROJECT=Path(__file__).resolve().parents[1]
STORE=PROJECT/'reports/leader-snapshots'
SLOTS=('09:40','09:45','09:50','14:30','15:00')
ORIGINS={'analysis_capture':'분석 직후 저장','saved_result_restored':'기존 분석 결과 사후 보관','posthoc_reference':'저장 관측값 사후 복원'}


def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def once(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('x',encoding='utf8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False)
        return True
    except FileExistsError:return False


def classify_market(observations):
    changes=[(observations.get('domestic_current',{}).get(m) or {}).get('change_pct') for m in ('KOSPI','KOSDAQ')]
    if any(value is None for value in changes):return '판단 자료 부족','두 국내 지수의 당시 등락률이 확보되지 않았습니다.'
    try:a,b=map(lambda value:Decimal(str(value)),changes)
    except Exception:return '판단 자료 부족','지수 등락률 확인 불가'
    if not a.is_finite() or not b.is_finite():return '판단 자료 부족','지수 등락률 확인 불가'
    if min(a,b)<=-3 or max(a,b)>=3:return '급락장 / 특이장','국내 지수 중 하나가 ±3% 이상 변동한 참고 분류입니다.'
    if min(a,b)>=1:return '강세장 / 불장','두 국내 지수가 모두 1% 이상 상승한 참고 분류입니다.'
    if max(a,b)<=-1:return '약세장 / 하락장','두 국내 지수가 모두 1% 이상 하락한 참고 분류입니다.'
    return '보통장 / 혼조장','두 국내 지수의 방향·강도가 엇갈리거나 변동이 제한적입니다.'


def market_context(as_of,*,project=PROJECT,observations=None):
    cutoff=instant(as_of)
    common=dict(domestic_previous=dict(KOSPI=None,KOSDAQ=None),nasdaq_previous=None,adr20={},issues=[])
    current=dict(domestic_current=dict(KOSPI=None,KOSDAQ=None),nasdaq_futures=None,domestic_night_futures=None,global_issues=[])
    # A datum needs both its observation and availability time. A prior date
    # alone is not proof that the stored derived value existed at that cutoff.
    for key,value in (observations or {}).items():
        if key=='domestic_current':
            for market,item in value.items():
                try:
                    if instant(item['observed_at'])<=cutoff and instant(item['available_at'])<=cutoff:current[key][market]=deepcopy(item)
                except (KeyError,ValueError,TypeError):pass
        elif key in ('nasdaq_futures','domestic_night_futures'):
            try:
                if instant(value['observed_at'])<=cutoff and instant(value['available_at'])<=cutoff:current[key]=deepcopy(value)
            except (KeyError,ValueError,TypeError):pass
    config=read(Path(project)/'config/analysis-paths.json')
    source=Path(config.get('analysis_adr_db','diagnostics/lightweight-20261105/adr-60.db'))
    if not source.is_absolute():source=Path(project)/source
    if source.is_file():
        try:
            db=sqlite3.connect(source.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
            try:
                meta=db.execute("SELECT value FROM aq_meta WHERE key='report'").fetchone()
                available=json.loads(meta[0]).get('created_at') if meta else None
                common['adr_available_at']=available
                if available and instant(available)<=cutoff:
                    for market in ('KOSPI','KOSDAQ'):
                        row=db.execute("SELECT day,value,status FROM adr20 WHERE market=? AND universe='common' AND day<? ORDER BY day DESC LIMIT 1",(market,cutoff.date().isoformat())).fetchone()
                        if not row:continue
                        breadth=db.execute("SELECT advances,declines FROM breadth_daily WHERE day=? AND market=? AND universe='common'",(row[0],market)).fetchone()
                        common['adr20'][market]=dict(value=row[1],basis_date=row[0],available_at=available,status=row[2],
                            advances=breadth[0] if breadth else None,declines=breadth[1] if breadth else None,source=str(source.resolve()))
                else:common['adr_status']='분석 시각 이후 생성되었거나 생성 시각 불명인 자료 제외'
            finally:db.close()
        except (sqlite3.Error,ValueError,TypeError):common['adr_status']='미수집'
    state,reason=classify_market(current)
    lines=['[시장 환경] '+state]
    if common['adr20']:
        basis=next(iter(common['adr20'].values()))['basis_date']
        label='전일 ADR20' if (cutoff.date()-datetime.fromisoformat(basis).date()).days==1 else '최근 저장 ADR20'
        lines.append(label+' '+ ' / '.join(m+' '+format(Decimal(v['value']),'.2f') for m,v in common['adr20'].items())+' · '+basis+' 잠정값')
    else:lines.append('당시 이용 가능한 ADR20 미수집')
    lines.append('지수·Nasdaq·선물: '+('일부 관측값 있음' if any(current['domestic_current'].values()) else '미수집'))
    return dict(common=common,observations=current,state=state,summary=lines,explanation=reason,rule_version='simple-index-change-v1')


def snapshot_path(root,as_of):
    at=instant(as_of)
    if at.strftime('%H:%M') not in SLOTS:raise ValueError('unsupported_selection_slot')
    return Path(root)/at.date().isoformat()/(at.strftime('%H%M')+'.json')


def persist(record,*,root=STORE,market=None):
    path=snapshot_path(root,record['analysis_at'])
    if path.exists():return dict(status='preserved',path=str(path))
    record=deepcopy(record);at=instant(record['analysis_at'])
    market=market or market_context(record['analysis_at'])
    raw=json.dumps(market['common'],sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf8')
    common_name='market-'+hashlib.sha256(raw).hexdigest()[:16]+'.json'
    once(path.parent/common_name,market['common'])
    record.update(schema_version='c16-selection-snapshot-v1',trade_date=at.date().isoformat(),slot=at.strftime('%H:%M'),
        session='AM' if at.hour<12 else 'PM',recorded_at=datetime.now(timezone.utc).isoformat())
    record['market']={k:deepcopy(v) for k,v in market.items() if k!='common'}
    record['market']['common_file']=common_name
    return dict(status='saved' if once(path,record) else 'preserved',path=str(path))


class News(HTMLParser):
    def __init__(self,html):
        super().__init__();self.level=0;self.active=None;self.summary=False;self.caption=False;self.item=None;self.anchor=None
        self.result=dict(summary='',articles=[],frozen=True);self.feed(html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag not in ('img','br','meta','input','link','hr'):self.level+=1
        if tag=='details' and 'news-toggle' in a.get('class','').split():self.active=self.level
        if tag=='summary' and self.active:self.summary=True
        if tag=='p' and a.get('class')=='news-comment':self.caption=True
        if self.active and tag=='li':self.item=dict(text='',article=None)
        if self.active and tag=='a':self.anchor=dict(title='',url=a.get('href',''))
    def handle_data(self,value):
        if self.summary or self.caption:self.result['summary']+=value
        if self.item is not None:self.item['text']+=value
        if self.anchor is not None:self.anchor['title']+=value
    def handle_endtag(self,tag):
        if tag=='summary':self.summary=False
        if tag=='p':self.caption=False
        if tag=='a' and self.anchor is not None:
            if self.item is not None:self.item['article']=self.anchor
            self.anchor=None
        if tag=='li' and self.item is not None:
            article=self.item['article']
            if article:
                tail=self.item['text'].split(' — ',1)
                if len(tail)==2 and ' / ' in tail[1]:article['source'],article['published_at']=tail[1].rsplit(' / ',1)
                self.result['articles'].append(article)
            self.item=None
        if tag=='details' and self.active==self.level:self.active=None
        self.level-=1


def frozen_news(news,*,common=False):
    from .c16_news import material_comment
    from .c16_ui import display_articles
    articles=display_articles((news or {}).get('articles',[]))[:5]
    return dict(summary=(news or {}).get('summary') or (material_comment(articles[0]) if articles else
                '공통 특징주 재료 없음' if common else '관련 상승 재료 기사 없음'),
        articles=[{key:a.get(key) for key in ('title','url','source','published_at')} for a in articles],frozen=True)


def capture_am_view(view,*,root=STORE,source='',origin='saved_result_restored',frame=None,charts=None,asset_root=None,market=None):
    at=view['reference_at'];path=snapshot_path(root,at)
    if path.exists():return dict(status='preserved',path=str(path))
    groups=deepcopy(view['analysis']['sectors'][:3]);stocks=[]
    chart_items={i['stock_code']:i for i in (charts or {}).get('items',[])}
    for group in groups:
        news=group.get('news') or {};companies={}
        sector_fragment=next((m.group(0) for m in re.finditer(r'<article class="sector-rank-card" data-sector-rank="(\d+)">.*?</article>',frame or '',re.S) if m[1]==str(group['rank'])),None)
        shared=News(sector_fragment).result if sector_fragment else frozen_news(dict(summary=news.get('summary'),articles=news.get('common_articles',[])),common=True)
        for leader in group['leaders'][:3]:
            code=leader['stock_code'];name=leader['name'];m=group.get('representative_metrics',{}).get(code,{})
            snippet=None
            for item in re.finditer(r'<li class="representative-line">.*?</li>(.*?)(?=<li class="representative-line">|</ol>)',frame or '',re.S):
                if '>'+escape(name)+'</span>' in item.group(0):snippet=item[1];break
            note=News(snippet).result if snippet else frozen_news(news.get('companies',{}).get(code))
            companies[code]=note
            chart=chart_items.get(code,{}).get('mini_chart_path')
            chart=str((Path(asset_root)/chart).resolve()) if chart and asset_root else None
            stocks.append(dict(stock_code=code,stock_name=name,rank=len(stocks)+1,sector_rank=group['rank'],sectors=[group['name']],
                change_pct=(m.get('change') or {}).get('raw_value',leader.get('raw_change')),
                turnover_eok=(m.get('turnover') or {}).get('eok'),market_cap_won=(m.get('capital') or {}).get('won'),
                capital_basis_date=(m.get('capital') or {}).get('basis_date'),news=note,chart_path=chart,
                chart_analysis_at=(charts or {}).get('generated_at'),measurements=deepcopy(m)))
        group['news']=dict(companies=companies,frozen_common=shared)
        # Saved view fields, not the full input/collection report.
        for key in list(group):
            if key not in ('rank','name','rising_count','turnover_eok','leaders','representative_metrics','news'):del group[key]
    return persist(dict(analysis_at=at,origin=origin,status='보류' if not groups else '참고 후보' if view.get('candidate_confirmed',False) is False else '정상',
        selection=dict(groups=groups,stocks=stocks,final_eligible_candidates=None),
        quality=dict(candidate_confirmed=False,source_result=source,source_presence=deepcopy(view.get('source_presence',{})),
            turnover_freshness=deepcopy(view.get('turnover_freshness')),
            chart_posthoc=any(s['chart_analysis_at'] and instant(s['chart_analysis_at'])>instant(at) for s in stocks))),root=root,market=market)


def capture_am(briefing,*,root=STORE,source='',origin='analysis_capture'):
    if snapshot_path(root,briefing['reference_at']).exists():return dict(status='preserved')
    from .c16_display import grouped_snapshot
    return capture_am_view(grouped_snapshot(briefing),root=root,source=source,origin=origin)


def capture_pm(data,*,root=STORE,source='',origin='posthoc_reference',frame=None,asset_root=None,market=None):
    at=data.get('analysis_at') or data['selection_cutoff_at'];path=snapshot_path(root,at)
    if path.exists():return dict(status='preserved',path=str(path))
    reference=data['reference'];rows=deepcopy(reference['candidates']);fragments=re.findall(r'<article class="top5-card"[^>]*>.*?</article>',frame or '',re.S)
    from .c16_news import cached_summary
    for i,row in enumerate(rows):
        name=row['stock_name'];code=row['stock_code'];row['rank']=i+1
        fragment=fragments[i] if len(fragments)>i else None
        if fragment and name in fragment:row['news']=News(fragment).result
        else:
            news=cached_summary(' / '.join(row.get('sectors',[])),[dict(name=name,stock_code=code)],at)
            row['news']=frozen_news(news.get('companies',{}).get(code))
        image=re.search(r'<img[^>]+src="([^"]+)"',fragment or '')
        row['chart_path']=str((Path(asset_root)/image[1]).resolve()) if image and asset_root else None
    diagnostic=''
    if frame:
        body=frame.split('<body>',1)[-1].split('<section id="c12-top5"',1)[0]
        diagnostic=re.sub(r'<[^>]+>',' ',body)
    final=re.search(r'최종 적격 후보\s*(\d+)개',diagnostic)
    return persist(dict(analysis_at=at,origin=origin,status='참고 후보',selection=dict(candidates=rows,sectors=reference['sectors'],
        final_eligible_candidates=deepcopy(data.get('confirmed_candidates')),final_eligible_count=(len(data['confirmed_candidates']) if 'confirmed_candidates' in data else int(final[1]) if final else None),execution_allowed=False),
        quality=dict(candidate_confirmed=False,reference_only=True,source_result=source,
            observations=deepcopy(data.get('replay',{})),source_diagnostics=diagnostic)),root=root,market=market)


def records(root=STORE):
    result=[]
    for path in sorted(Path(root).glob('????-??-??/????.json')):
        data=read(path)
        if data.get('schema_version')=='c16-selection-snapshot-v1':result.append(data)
    return result


def render_record(record,output):
    from .c16_ui import ranked_board,REPRESENTATIVE_CSS,NEWS_CSS,RANK_CSS
    from .top5_dashboard import CSS,COMPACT_CSS
    rel=lambda value:os.path.relpath(value,output).replace('\\','/') if value else None
    if record['session']=='PM':
        from .c16_sessions import pm_frame
        rows=deepcopy(record['selection']['candidates'])
        for row in rows:row['chart_path']=rel(row.get('chart_path'))
        data=dict(day=record['trade_date'],slot=record['slot'],analysis_at=record['analysis_at'],status='available',candidates=rows,
            reference_only=True,frozen_snapshot=True,frozen_news={r['stock_code']:r['news'] for r in rows})
        return pm_frame(data,output)
    body=ranked_board(dict(analysis=dict(sectors=deepcopy(record['selection']['groups']))),record['slot'])
    cards=[]
    for stock in record['selection']['stocks']:
        image=rel(stock.get('chart_path'))
        cards.append('<article class="top5-card" data-sector-rank="'+str(stock['sector_rank'])+'"><h3>'+escape(stock['stock_name'])+'</h3>'+('<a target="_blank" rel="noopener" href="'+escape(image,quote=True)+'"><img src="'+escape(image,quote=True)+'" alt="'+escape(stock['stock_name'],quote=True)+' 미니차트"></a>' if image else '<p>저장된 차트 없음</p>')+'</article>')
    body+='<section id="c12-top5" class="top5-compact"><div class="top5-grid">'+''.join(cards)+'</div></section>'
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>body{font-family:system-ui;background:#f5f7fa;color:#243447;padding:12px}.sector-rank-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.sector-rank-card{padding:10px;border-radius:8px}.momentum-track{height:7px;background:#edf1f6;border-radius:4px}.momentum-bar{height:100%}.trader-top3,.trader-momentum{padding:10px;background:white;margin:8px 0}@media(max-width:1100px){.sector-rank-grid{grid-template-columns:1fr}}'+CSS+COMPACT_CSS+REPRESENTATIVE_CSS+NEWS_CSS+RANK_CSS+'</style></head><body>'+body+'</body></html>'


def calendar_page(*,root=STORE,output):
    entries=[]
    for record in records(root):
        entries.append(dict(day=record['trade_date'],slot=record['slot'],session=record['session'],status=record['status'],
            origin=ORIGINS.get(record['origin'],record['origin']),recorded_at=record['recorded_at'],
            summary=record['market']['summary'],explanation=record['market']['explanation'],observations=record['market']['observations'],
            common=read(Path(root)/record['trade_date']/record['market']['common_file']),html=render_record(record,output)))
    payload=json.dumps(entries,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    script='const RECORDS='+payload+';'+r'''
const $=id=>document.getElementById(id);let month=(RECORDS.at(-1)?.day||new Date().toISOString().slice(0,10)).slice(0,7),selected=null;
function button(text,fn){const b=document.createElement('button');b.textContent=text;b.addEventListener('click',fn);return b;}
function shift(delta){const [y,m]=month.split('-').map(Number),d=new Date(y,m-1+delta,1);month=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');draw();}
function draw(){const [y,m]=month.split('-').map(Number),grid=$('days');$('month').textContent=month;grid.replaceChildren();
 for(let n=0;n<new Date(y,m-1,1).getDay();n++)grid.append(document.createElement('span'));
 for(let day=1;day<=new Date(y,m,0).getDate();day++){const date=month+'-'+String(day).padStart(2,'0'),rows=RECORDS.filter(r=>r.day===date),b=button(String(day)+(rows.length?' ●':''),()=>pick(date));b.className=rows.length?'recorded':'';b.setAttribute('aria-label',date+(rows.length?' 저장 '+rows.length+'건':' 저장된 기록 없음'));grid.append(b);}}
function pick(day){selected=day;$('selected').textContent=day;$('times').replaceChildren();const rows=RECORDS.filter(r=>r.day===day);if(!rows.length){$('market').textContent='저장된 기록 없음';$('origin').textContent='';$('evidence').textContent='';$('view').removeAttribute('srcdoc');$('view').hidden=true;return;}
 for(const [label,slots] of [['오전 당일매매',['09:40','09:45','09:50']],['오후 종가매매',['14:30','15:00']]]){const section=document.createElement('div'),name=document.createElement('b');name.textContent=label+' ';section.append(name);for(const slot of slots)section.append(button(slot,()=>show(day,slot)));$('times').append(section);}
 show(day,rows.find(r=>r.slot==='09:50')?.slot||rows.at(-1).slot);}
function show(day,slot){const r=RECORDS.find(r=>r.day===day&&r.slot===slot);$('view').hidden=!r;if(!r){$('market').textContent='저장된 기록 없음';$('origin').textContent=day+' '+slot;$('evidence').textContent='';$('view').removeAttribute('srcdoc');return;}
 $('market').textContent=r.summary.join('\n');$('origin').textContent=day+' '+slot+' · '+r.origin+' · '+r.status+' · 저장 '+r.recorded_at;
 const fields=['분류 근거: '+r.explanation];for(const m of ['KOSPI','KOSDAQ']){const v=r.observations.domestic_current[m],p=r.common.domestic_previous[m];fields.push(m+' 전일 지수: '+(p?p.level+' / '+p.change_pct+'%':'미수집'));fields.push(m+' 분석 시점: '+(v?v.level+' / '+v.change_pct+'% · 5일선 '+(v.ma5_position||'미수집')+' / 20일선 '+(v.ma20_position||'미수집')+' · 관측 '+v.observed_at:'미수집'));}
 fields.push('전일 Nasdaq: '+(r.common.nasdaq_previous?.level||'미수집'),'Nasdaq 선물: '+(r.observations.nasdaq_futures?.level||'미수집'),'국내 야간선물: '+(r.observations.domestic_night_futures?.level||'미수집'));
 fields.push(...Object.entries(r.common.adr20||{}).map(([m,v])=>m+' '+v.basis_date+' ADR20 '+Number(v.value).toFixed(2)+' / 상승 '+v.advances+' / 하락 '+v.declines+' / 자료 생성 '+v.available_at));$('evidence').textContent=fields.join('\n');
 $('view').srcdoc=r.html;}
$('prev').addEventListener('click',()=>shift(-1));$('next').addEventListener('click',()=>shift(1));draw();if(RECORDS.length)pick(RECORDS.at(-1).day);
'''
    digest=base64.b64encode(hashlib.sha256(script.encode('utf8')).digest()).decode('ascii')
    style='body{font-family:system-ui;color:#243447;background:#f5f7fa;margin:12px}button{border:1px solid #cbd5e1;border-radius:6px;background:white;padding:7px 12px;cursor:pointer;margin:2px}button:focus-visible{outline:2px solid #64748b}.month{display:flex;gap:12px;align-items:center}.grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));max-width:560px}.recorded{background:#e7effb;font-weight:700}#market,#evidence{white-space:pre-line;font-size:13px}#origin{font-size:12px;color:#64748b}#view{width:100%;height:900px;border:0}#times{margin:10px 0}'
    page='<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>'+style+'</style></head><body><h2>주도주 캘린더</h2><div class="month"><button id="prev" aria-label="이전 달">이전 달</button><b id="month"></b><button id="next" aria-label="다음 달">다음 달</button></div><div class="grid">'+''.join('<span>'+day+'</span>' for day in ('일','월','화','수','목','금','토'))+'</div><div id="days" class="grid"></div><h3 id="selected">날짜를 선택하세요</h3><p id="origin"></p><p id="market">저장된 기록 없음</p><details><summary>시장 자료</summary><p id="evidence"></p></details><div id="times"></div><iframe id="view" title="저장된 선정 기록" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" hidden></iframe><script>'+script+'</script></body></html>'
    return page,digest


def attach_calendar(html,*,root=STORE,output):
    page,digest=calendar_page(root=root,output=output)
    if 'id="session-calendar"' not in html:
        marker='<label for="session-pm">오후 종가매매</label>'
        html=html.replace(marker,marker+'<input class="sr-only" type="radio" name="session" id="session-calendar"><label for="session-calendar">주도주 캘린더</label>',1)
    html=re.sub(r'<section class="session-panel" id="panel-calendar">.*?</section>','',html,flags=re.S)
    panel='<section class="session-panel" id="panel-calendar"><iframe class="session-frame" title="주도주 캘린더" sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox" srcdoc="'+escape(page,quote=True)+'"></iframe></section>'
    html=html.replace('</main>',panel+'</main>',1)
    # The session stylesheet hides every .session-frame by default. The
    # calendar needs its own checked-tab rule for both the panel and iframe.
    html=re.sub(r'<style id="calendar-style">.*?</style>','',html,flags=re.S)
    html=html.replace('</head>','<style id="calendar-style">.session-root:has(#session-calendar:checked) #panel-calendar,.session-root:has(#session-calendar:checked) #panel-calendar>.session-frame{display:block}.tab-controls:has(#session-calendar:checked) label[for=session-calendar]{background:#26394d;color:white}</style></head>',1)
    def policy(match):
        value=re.sub(r"script-src[^;]*;?",'',match[2]).strip()
        return match[1]+value+"; script-src 'sha256-"+digest+"';"+match[3]
    return re.sub(r'(<meta http-equiv="Content-Security-Policy" content=")([^"]*)(">)',policy,html,count=1)
