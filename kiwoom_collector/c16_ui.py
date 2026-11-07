"""Presentation-only compression of saved C15/C16 Radar frames."""
from html import escape
from math import sqrt
from decimal import Decimal
from .theme_normalization import SECURITY_DETAILS
from .c15_briefing_data import format_capital

CSS='''
.trader-top3{padding:10px 14px;background:white;border:1px solid #dbe3eb;border-radius:10px;margin:8px 0}
.trader-bubbles{display:flex;gap:24px;align-items:center;justify-content:center;padding:6px;flex-wrap:wrap}
.trader-bubble{border-radius:50%;background:#e7effb;border:2px solid #5c80b5;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:8px;box-sizing:border-box}
.trader-bubble b{font-size:15px}.trader-bubble small{font-size:11px}.trader-momentum{background:white;border:1px solid #dbe3eb;border-radius:10px;padding:10px 14px}
.momentum-row{display:grid;grid-template-columns:minmax(90px,1fr) 45px 65px minmax(70px,1fr);gap:10px;align-items:center;margin:6px 0;font-size:13px}
.momentum-track{height:7px;background:#edf1f6;border-radius:4px;overflow:hidden}.momentum-bar{height:100%;background:#6a8cae}
.momentum-up{color:#b44242}.momentum-down{color:#3267a3}.trader-note{font-size:11px;color:#64748b}
.trader-details{border:1px solid #dbe3eb;border-radius:8px;margin:10px 0;padding:10px;background:white}
.trader-details>summary{font-weight:600;cursor:pointer}.trader-details .c15-linked{display:none}
#c12-top5.top5-compact{padding:10px 0}#c12-top5.top5-compact .top5-grid{gap:10px}
#c12-top5.top5-compact .top5-card{padding:8px 10px}#c12-top5.top5-compact h3{margin:0 0 1px}
#c12-top5.top5-compact .compact-rank{margin:0 0 2px}#c12-top5.top5-compact .compact-chart{aspect-ratio:960/370}
#c12-top5.top5-compact .compact-close{font-size:12px;color:#475569;margin:0}
'''

REPRESENTATIVE_CSS='''
.sector-rank-grid{gap:22px}
.trader-momentum>.sector-rank-grid>article{padding:10px 12px;border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc}
.representative-list{list-style:none;padding:0;margin:7px 0}
.representative-line{display:grid;width:100%;grid-template-columns:minmax(0,1fr) 56px 104px 84px;gap:8px;align-items:center;white-space:nowrap;font-size:12px;margin:5px 0}
.representative-name{min-width:0;overflow:hidden;text-overflow:ellipsis;text-align:left;font-size:18px;font-weight:700}
.representative-name+.representative-number{font-size:16px;font-weight:600}
.representative-number{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
@media(max-width:1100px){.trader-momentum>.sector-rank-grid{grid-template-columns:1fr}}
@media(max-width:380px){.representative-line{grid-template-columns:minmax(0,1fr) 48px 92px 76px;gap:6px}}
'''

NEWS_CSS='''
.company-news{list-style:none;margin:0 0 10px;font-size:12px;color:#475569}
.news-comment{margin:3px 0;font-size:12px;font-weight:400;color:#475569}
.news-toggle>summary{list-style:none;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:400}
.news-toggle>summary::-webkit-details-marker{display:none}
.news-toggle>summary::before{content:"▸ ";}.news-toggle[open]>summary::before{content:"▾ ";}
.news-toggle>summary:focus-visible{outline:2px solid #64748b;outline-offset:2px}
.news-toggle ul{margin:5px 0 8px;padding-left:18px;white-space:normal;overflow-wrap:anywhere}
.news-toggle li{margin:4px 0;font-size:12px;font-weight:400}
'''


def representative_row(name,rate,cash,capital):
    values=(name,rate,cash if cash.startswith('거래대금 ') else '거래대금 '+cash,
            capital if capital.startswith('시총 ') else '시총 '+capital)
    return '<li class="representative-line">'+''.join('<span class="'+('representative-name' if i==0 else 'representative-number')+'" title="'+escape(value,quote=True)+'">'+escape(value)+'</span>' for i,value in enumerate(values))+'</li>'


def display_articles(articles):
    """Compact only the visible list; leave cached articles and comments intact."""
    import re
    from difflib import SequenceMatcher
    from urllib.parse import urlparse,parse_qsl,urlencode,urlunparse
    from .am_time_policy import instant
    from .c16_news import clean_text

    def url_key(article):
        p=urlparse(article['url'])
        query=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_')
               and k.lower() not in ('fbclid','gclid','ref','referrer')
               and not (p.hostname=='news.google.com' and k.lower()=='oc')
               and not ((p.hostname or '').endswith('yna.co.kr') and k.lower()=='input')]
        return urlunparse(('',p.netloc.lower(),p.path.rstrip('/'),'',urlencode(sorted(query)),''))

    def text(article,field):
        value=clean_text(article.get(field,'')).lower()
        source=(article.get('source') or '').lower()
        if source:value=value.removesuffix(' - '+source)
        value=re.sub(r'(\d+)천(\d+)',lambda m:str(int(m[1])*1000+int(m[2])),value.replace(',',''))
        return value

    def title_key(article):
        value=text(article,'title')
        for name in article.get('matched_names',[]):value=value.replace(name.lower(),'')
        value=re.sub(r'(?:트럼프\s*)?(?:車|자동차)\s*정책|(?:美\s*)?관세','관세',value)
        value=value.replace('후퇴','완화').replace('기대감','기대')
        value=value.replace('만의','만')
        value=re.sub(r'\d+(?:[.~～-]\d+)?(?:\.\d+)?\s*%대?|특징주|장\s*초반|장중|종합|급등|강세|약세|하락|상승|불기둥|들썩|영향|마감|분기','',value)
        return re.sub(r'[^a-z0-9가-힣]','',value)

    def facts(article):
        title=text(article,'title')+' '+text(article,'description')
        # Contract amounts, clinical phases, explicit announcement dates and
        # revisions are new information, unlike changing intraday percentages.
        numbers={re.sub(r'\s+','',value).replace('억원','억').replace('조원','조')
                 for value in re.findall(r'\d+(?:\.\d+)?\s*(?:억원|억|조원|조|만주|mw|gw|상|분기)',title)}
        numbers.update(re.findall(r'(?:관세|세율|금리|지분율)\s*\d+(?:\.\d+)?%',title))
        dates=set(re.findall(r'\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}월\s*\d{1,2}일',text(article,'title')))
        dates.update(re.findall(r'(\d{1,2}월\s*\d{1,2}일).{0,12}(?:발표|계약|공시|승인)',text(article,'description')))
        updates=set(re.findall(r'추가\s*수주|신규\s*계약|계약\s*변경|계약\s*취소|정정|철회|부인|성공|실패|불발|중단|재개',title))
        return numbers,dates,updates

    def same(a,b):
        if url_key(a)==url_key(b):return True
        # Do not collapse a fresh announcement merely because its title repeats.
        if instant(a['published_at']).date()!=instant(b['published_at']).date():return False
        fa,fb=facts(a),facts(b)
        if any(x and y and x!=y for x,y in zip(fa[:2],fb[:2])) or fa[2]!=fb[2]:return False
        x,y=title_key(a),title_key(b)
        if x==y and x:return True
        # Short, explicit trade-policy headlines often paraphrase the same item.
        if all('관세' in key and '완화' in key and '기대' in key for key in (x,y)):return True
        if all('자사주처분' in key for key in (x,y)):return True
        title_similarity=SequenceMatcher(None,x,y,autojunk=False).ratio()
        if min(len(x),len(y))>=10 and title_similarity>=.70:return True
        x,y=text(a,'description'),text(b,'description')
        return title_similarity>=.45 and min(len(x),len(y))>=60 and SequenceMatcher(None,x,y,autojunk=False).ratio()>=.86

    def priority(article):
        # Prefer an existing publisher URL over an aggregator wrapper; no probes.
        direct=urlparse(article['url']).hostname not in ('news.google.com','news.naver.com','n.news.naver.com')
        return direct,instant(article['published_at'])

    groups=[]
    for article in sorted(articles,key=lambda a:instant(a['published_at']),reverse=True):
        group=next((group for group in groups if any(same(article,member) for member in group)),None)
        if group is None:groups.append([article])
        else:group.append(article)
    return sorted((max(group,key=priority) for group in groups),key=lambda a:instant(a['published_at']),reverse=True)


def news_toggle(articles,empty='관련 상승 재료 기사 없음',comment=None,frozen=False):
    """Show only an extract of supported material; never invent a catalyst."""
    from urllib.parse import urlparse
    from .c16_news import material_comment
    articles=[a for a in articles if urlparse(a.get('url','')).scheme in ('http','https')]
    if not articles:return '<p class="news-comment">'+escape(empty)+'</p>'
    comment=comment or material_comment(articles[0])
    articles=articles if frozen else display_articles(articles)[:5]
    def link(article):
        source=article.get('source') or urlparse(article['url']).hostname or article.get('provider','뉴스')
        return ('<li><a target="_blank" rel="noopener noreferrer" href="'+escape(article['url'],quote=True)+'">'
                +escape(article['title'])+'</a> — '+escape(str(source))+' / '
                +escape(str(article.get('published_at','')))+'</li>')
    return '<details class="news-toggle news-comment"><summary>'+escape(comment)+'</summary><ul>'+''.join(link(a) for a in articles)+'</ul></details>'


def company_news(news):
    return '<li class="company-news">'+news_toggle((news or {}).get('articles',[]),comment=(news or {}).get('summary'),frozen=(news or {}).get('frozen',False))+'</li>'


def sector_news(news,leaders):
    from .c16_news import common_articles
    if (news or {}).get('frozen_common') is not None:
        frozen=news['frozen_common']
        return news_toggle(frozen.get('articles',[]),empty='공통 특징주 재료 없음',comment=frozen.get('summary'),frozen=True)
    articles=common_articles((news or {}).get('articles',[]),leaders)
    return news_toggle(articles,empty='공통 특징주 재료 없음')


def attach_chart_news(html,sectors,as_of):
    """Add cached evidence under existing charts; preserve chart/Radar markup."""
    import re
    from .c16_news import cached_summary
    companies={}
    for sector in sectors[:3]:
        leaders=sector['leaders'][:3]
        for code,news in cached_summary(sector['name'],leaders,as_of).get('companies',{}).items():
            companies.setdefault(code,news)
    def replace(match):
        card=match.group(0)
        card=re.sub(r'<div class="chart-news">.*?</div>','',card,flags=re.S)
        news=company_news(companies.get(match.group(1)))
        block='<div class="chart-news"><p>뉴스 기준 시각: '+escape(as_of)+'</p><ul style="list-style:none;padding:0">'+news+'</ul></div>'
        return card.removesuffix('</article>')+block+'</article>'
    return re.sub(r'<article class="top5-card"[^>]*data-code="([0-9]+)"[^>]*>.*?</article>',replace,html,flags=re.S)


RANK_CSS='''
[data-sector-rank="1"]{--rank-dark:#F97316;--rank-edge:#F97316;--rank-tint:#FFF0E5}
[data-sector-rank="2"]{--rank-dark:#075e59;--rank-edge:#298e82;--rank-tint:#e7f6f3}
[data-sector-rank="3"]{--rank-dark:#59318d;--rank-edge:#8b64b6;--rank-tint:#f2ebfb}
.sector-rank-card[data-sector-rank],.trader-momentum>.sector-rank-grid>.representative-sector[data-sector-rank],#c12-top5 .top5-card[data-sector-rank]{border:1px solid var(--rank-edge);background:var(--rank-tint);overflow:hidden}
.sector-rank-card[data-sector-rank]>h3,.representative-sector[data-sector-rank]>h3,#c12-top5 .top5-card[data-sector-rank]>h3{background:var(--rank-dark);color:#fff;padding:8px 10px;border-radius:5px;margin:0 0 8px}
.sector-rank-card[data-sector-rank] .momentum-bar{background:var(--rank-dark)}
#c12-top5 .chart-sector-title[data-sector-rank]{background:var(--rank-dark);color:#fff;border-radius:6px;padding:8px 12px}
'''


def momentum_board(data):
    groups=[g for g in data['groups'] if g['count']>0 and g['name']!='미분류']
    byname={g['name']:g for g in groups};folded=[]
    # Existing explicit parent rules only; no substring/overlap hierarchy guesses.
    for group in groups:
        parent='보안' if group['name'] in SECURITY_DETAILS else '반도체 소부장' if group['name']=='기판·PCB' else None
        members={s['stk_cd'] for s in group['current']['stocks']}
        if parent in byname and members<={s['stk_cd'] for s in byname[parent]['current']['stocks']}:
            folded.append(group['name'])
    visible=[g for g in groups if g['name'] not in folded][:5]
    rows=[]
    for group in visible:
        delta=group['delta20'];label='—' if delta is None else '↑ +'+str(delta) if delta>0 else '↓ '+str(delta) if delta<0 else '→ 0'
        tone='momentum-up' if delta and delta>0 else 'momentum-down' if delta and delta<0 else ''
        rows.append('<div class="momentum-row"><b>'+escape(group['name'])+'</b><span>'+str(group['count'])+'종목</span><span class="'+tone+'">'+label+'</span><div class="momentum-track"><div class="momentum-bar" style="width:'+str(group['count']/20*100)+'%"></div></div></div>')
    previous=data.get('previous')
    counts='신규 '+str(len(data.get('entrants') or []))+' · 유지 '+str(len(data.get('retained') or []))+' · 이탈 '+str(len(data.get('exits') or [])) if previous else '직전 관측 없음 · 신규/유지/이탈 미확인'
    return '<section class="trader-momentum"><h2>Sector Radar 관심 변화 TOP5</h2>'+''.join(rows)+'<p>'+counts+'</p><p class="trader-note">0198 TOP20 포함 종목 수 변화 · 주가 등락률/매수 신호 아님 · C15 순위와 별도</p>'+('<details><summary>부모 테마의 세부 테마</summary>'+''.join('<p>'+escape(n)+' · '+str(byname[n]['count'])+'종목</p>' for n in folded)+'</details>' if folded else '')+'</section>'


def c15_bubbles(snapshot,slot):
    if not snapshot:return '<section class="trader-top3"><h2>C15 결과 미확인 · '+slot+'</h2></section>'
    circles=[]
    for sector in snapshot['analysis']['sectors'][:3]:
        size=88+sqrt(sector['rising_count'])*12
        circles.append('<div class="trader-bubble" style="width:'+str(size)+'px;height:'+str(size)+'px"><small>'+str(sector['rank'])+'위</small><b>'+escape(sector['name'])+'</b><small>상승 관측 '+str(sector['rising_count'])+'종목</small></div>')
    return '<section class="trader-top3"><h2>C15 주도섹터 TOP3 · '+slot+'</h2><div class="trader-bubbles">'+''.join(circles)+'</div></section>'


def ranked_board(snapshot,slot):
    if not snapshot:return c15_bubbles(None,slot)
    groups=snapshot['analysis']['sectors'];maximum=max((Decimal(g['turnover_eok'] or 0) for g in groups),default=Decimal(0))
    cards=[];leaders=[]
    for group in groups:
        value=Decimal(group['turnover_eok']) if group['turnover_eok'] is not None else None
        amount='대금 미확인' if value is None else (f'{value/10000:.2f}조' if value>=10000 else f'{value:,.0f}억')
        width=value/maximum*100 if value is not None and maximum else 0
        title=str(group['rank'])+'위 '+escape(group['name'])
        news=group.get('news') or dict(summary='상승 재료 미확인',articles=[])
        cards.append('<article class="sector-rank-card" data-sector-rank="'+str(group['rank'])+'"><h3>'+title+'</h3><p>포착 '+str(group['rising_count'])+'종목 · <b>'+amount+'</b></p><div class="momentum-track"><div class="momentum-bar" style="width:'+str(width)+'%"></div></div>'+sector_news(news,group['leaders'])+'</article>')
        lines=[]
        for leader in group['leaders']:
            m=group.get('representative_metrics',{}).get(leader['stock_code'],{})
            change=m.get('change',{}).get('raw_value');cash=m.get('turnover',{}).get('eok')
            rate=f'{Decimal(change):+.2f}%' if change is not None else '미확인'
            money=f'{Decimal(cash):,.0f}억' if cash is not None else '미확인'
            cap=format_capital(m.get('capital',{}).get('won')).removeprefix('시총 ')
            lines.append(representative_row(leader['name'],rate,money,cap))
            lines.append(company_news(news.get('companies',{}).get(leader['stock_code'])))
        leaders.append('<article class="representative-sector" data-sector-rank="'+str(group['rank'])+'"><h3>'+title+'</h3><ol class="representative-list">'+''.join(lines)+'</ol></article>')
    return '<section class="trader-top3"><h2>주도섹터 TOP3 · '+slot+'</h2><div class="sector-rank-grid">'+''.join(cards)+'</div><p class="trader-note">표시 순위: C15 상승 후보 묶음의 관측 거래대금 부분합 내림차순 · 관심도/C15 원본 점수 순위와 별도</p></section><section class="trader-momentum"><h2>섹터별 대표 종목</h2><div class="sector-rank-grid">'+''.join(leaders)+'</div><p class="trader-note">등락률·대금은 해당 0186 관측 기준 · 대금 누적 기준 미확인 · 시총은 기존 과거 종가 참고값</p></section>'


def attention_line(data):
    rising=[g['name']+' ↑' for g in data['groups'] if g.get('delta20') is not None and g['delta20']>0][:1]
    steady=[g['name']+' →' for g in data['groups'] if g.get('count',0)>0 and g.get('delta20')==0][:1]
    changes='신규 '+str(len(data.get('entrants') or []))+' / 이탈 '+str(len(data.get('exits') or [])) if data.get('previous') else '직전 관측 미확인'
    return '<p class="trader-attention">'+escape(' · '.join(rising+steady) or '관심 변화 미확인')+' | '+changes+'</p>'


def trader_frame(html,frame,snapshot,slot):
    """Move original Radar body, tables and entry/exit into a closed disclosure."""
    opening='<main class="bubble-root">';start=html.index(opening)+len(opening);end=html.rindex('</main>')
    body=html[start:end];cards=''
    if '<section id="c12-top5"' in body:
        section=body.index('<section id="c12-top5"');finish=body.index('</section>',section)+len('</section>')
        style=body.rfind('<style>',0,section)
        begin=style if style>=0 and body[style:section].count('</style>')==1 else section
        cards=body[begin:finish];body=body[:begin]+body[finish:]
    ranked=bool(snapshot and snapshot.get('display_ranking'))
    board=ranked_board(snapshot,slot) if ranked else c15_bubbles(snapshot,slot)+momentum_board(frame['data'])
    if ranked:
        evidence=[]
        for group in snapshot['display_ranking']['groups'][:3]:
            catalyst=group['catalyst']
            evidence.append('<p><b>관측 섹터: '+escape(group['name'])+'</b> · 원본 '+escape(' / '.join(group['source_names']))+'</p><p>상승 재료: '+escape(str(catalyst.get('description') or '미확인'))+' · '+escape(catalyst['status'])+'</p>')
        for group in snapshot['display_ranking'].get('held_groups',[]):
            evidence.append('<p>'+escape(group['name'])+' 표시 보류 · 독립 상승후보 '+str(group['rising_count'])+'개 · 원본 복수 소속 보존</p>')
        body='<section><h3>표시 분류 근거 · 상승 재료와 별도</h3>'+''.join(evidence)+'</section>'+body
    combined=board+attention_line(frame['data'])+cards+'<details class="trader-details"><summary>상세 분석 · 원본 C15 순위 · Radar · 신규/이탈</summary>'+body+'</details>'
    # UI overrides must follow the card stylesheet.
    combined+='<style>'+CSS+'.sector-rank-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.sector-rank-card{padding:10px;background:#f3f6fb;border-radius:8px}.chart-sector-title{grid-column:1/-1;margin-top:8px}.representative-line{white-space:nowrap;font-size:12px}.trader-momentum article{overflow-x:auto}.news-line,.trader-attention{font-size:12px}.sector-rank-card details{font-size:11px}@media(max-width:650px){.sector-rank-grid{grid-template-columns:1fr}}</style>'
    combined+='<style>'+REPRESENTATIVE_CSS+'</style>'
    combined+='<style>'+RANK_CSS+'</style>'
    combined+='<style>'+NEWS_CSS+'</style>'
    return html[:start]+combined+html[end:]
