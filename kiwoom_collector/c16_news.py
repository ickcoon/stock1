"""Bounded per-company feature-news search and cutoff-aware public article cache.

Not imported by Collector or Telegram. RSS headlines alone never establish cause.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlencode,urlparse,parse_qsl,urlunparse
from urllib.request import urlopen,Request
import xml.etree.ElementTree as ET
from .am_time_policy import instant
from .environment_file import values as credential_values

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'config/c16-news-reference.json'
SEARCH_CACHE=ROOT/'diagnostics/c16-news-search.json'
WINDOW_HOURS=72


def query_for(sector,names,as_of):
    names=list(dict.fromkeys(names))
    if len(names)!=1 or not names[0].strip():raise ValueError('one_company_name_required')
    return names[0].strip()+' 특징주'


def search_public(sector,names,as_of):
    query=query_for(sector,names,as_of)
    url='https://news.google.com/rss/search?'+urlencode(dict(q=query,hl='ko',gl='KR',ceid='KR:ko'))
    try:
        with urlopen(url,timeout=4) as response:
            raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('news_response_limit')
        articles=[]
        for item in ET.fromstring(raw).findall('./channel/item')[:100]:
            try:stamp=parsedate_to_datetime(item.findtext('pubDate')).isoformat()
            except (ValueError,TypeError,AttributeError):continue
            articles.append(dict(title=clean_text(item.findtext('title')),description=clean_text(item.findtext('description')),source=item.findtext('source'),
                published_at=stamp,url=item.findtext('link'),matched_codes=[],body_checked=False,
                provider='Google_News_RSS',query=query,
                attribution='related_news_only',published_time_basis='public_RSS_pubDate'))
        return dict(status='searched',query=query,provider_url=url,raw_articles=articles,
                    articles=relevant_articles(articles,sector,names,as_of),response_count=len(articles))
    except Exception as error:
        return dict(status='unavailable',query=query,provider_url=url,articles=[],error_type=type(error).__name__)



HUB_URL='https://naverapihub.apigw.ntruss.com/search/v1/news'


def hub_credentials():
    """Read only explicitly HUB-named credentials; never use Developers keys."""
    values=credential_values()
    return values.get('NAVER_API_HUB_KEY_ID'),values.get('NAVER_API_HUB_KEY')


def clean_text(value):
    return unescape(re.sub(r'<[^>]+>','',value or '')).strip()


def rising_article(article,name):
    """Simple headline/summary checks for the company's own positive material."""
    title=clean_text(article.get('title',''))
    title=re.sub(r'^(?:\[[^]]*\]|특징주[,：:]?)\s*','',title)
    if name not in title:return False
    first=title.split(',')[0].strip()
    if ',' in title and name not in first and re.fullmatch(r'[가-힣A-Za-z0-9·]+',first):return False
    clauses=[part.strip() for part in re.split(r'[,;…⋯]',title) if part.strip()]
    focus=' '.join(part for part in clauses if name in part)
    negative=r'하락|약세|급락|부진|매출\s*감소|실적\s*감소|적자|손실|취소|실패|하향|철회|부인|무산'
    if re.search(negative,focus):return False
    # "Company, new contract ..." keeps the following material clause, while
    # "Company A strong, Company B weak" is judged on its own named clause.
    if first==name:focus=title
    positive=r'상승|강세|급등|상한가|불기둥|들썩|수주|공급|계약|흑자|개선|호실적|신제품|출시|임상|승인|허가|확대|기대|수혜|완화|성장|신약|개발'
    if re.search(negative,focus):return False
    if re.search(positive,focus):return True
    description=clean_text(article.get('description',''))
    return title.startswith(name) and not re.search(negative,description) and bool(re.search(positive,description))


def relevant_articles(articles,sector,names,as_of,*,apply_window=True,require_related=True):
    # Relevance and publication cutoff only; no causal/trust assessment.
    result=[];seen={}
    for article in (summarize(articles,sector,[],as_of)['articles'] if apply_window else articles):
        text=article['title']+' '+article.get('description','')
        query=article.get('query','')
        bound=query.endswith(' 특징주')
        matched=[name for name in names if name and name in text
                 and (not bound or query==query_for('',[name],as_of) or name in article.get('matched_names',[]))
                 and ('특징주' in text or query==query_for('',[name],as_of))]
        if require_related:matched=[name for name in matched if rising_article(article,name)]
        if not matched and require_related:continue
        title=article['title']
        if article.get('provider')=='Google_News_RSS' and article.get('source'):
            title=title.removesuffix(' - '+article['source'])
        key=re.sub(r'\W','',title).lower()
        parsed=urlparse(article['url'])
        tracking=lambda k:k.lower().startswith('utm_') or k.lower() in ('fbclid','gclid')
        url_key=urlunparse(parsed._replace(fragment='',query=urlencode([(k,v) for k,v in parse_qsl(parsed.query) if not tracking(k)])))
        previous=seen.get(key) or seen.get(url_key)
        if not previous:
            # NAVER may shorten the same title that RSS supplies in full.
            for existing in result:
                other=re.sub(r'\W','',existing['title']).lower()
                shortened=title.endswith(('...','…')) or existing['title'].endswith(('...','…'))
                if shortened and min(len(key),len(other))>=25 and (key.startswith(other) or other.startswith(key)) and article.get('published_at') and existing.get('published_at') and abs((instant(article['published_at'])-instant(existing['published_at'])).total_seconds())<=300:
                    previous=existing;break
        if previous:
            previous['matched_names']=list(dict.fromkeys(previous['matched_names']+matched))
            previous['scope']='sector_common' if len(previous['matched_names'])>=2 else 'company'
            if len(title)>len(previous['title']):previous['title']=title
            if not previous.get('source') and article.get('source'):previous['source']=article['source']
            continue
        item=dict(article,scope='sector_common' if len(matched)>=2 else 'company',matched_names=matched)
        seen[key]=seen[url_key]=item
        result.append(item)
    def published_key(article):
        try:return instant(article['published_at']).timestamp()
        except (KeyError,ValueError,TypeError):return float('-inf')
    return sorted(result,key=published_key,reverse=True)


def article_counts(raw,name,as_of):
    unique=relevant_articles(raw,'',[name],as_of,apply_window=False,require_related=False)
    timed=summarize(unique,'',[],as_of)
    related=relevant_articles(timed['articles'],'',[name],as_of)
    return dict(parsed_count=len(raw),deduplicated_count=len(unique),time_pass_count=len(timed['articles']),
                related_count=len(related),rejected_reasons={reason:sum(r['reason']==reason for r in timed['rejected'])
                    for reason in {r['reason'] for r in timed['rejected']}})


def search_hub(sector,names,as_of):
    query=query_for(sector,names,as_of)
    try:key_id,key=hub_credentials()
    except (OSError,ValueError):
        return dict(status='credentials_unreadable',query=query,articles=[])
    if not key_id or not key:
        return dict(status='credentials_missing',query=query,articles=[])
    url=HUB_URL+'?'+urlencode(dict(query=query,display=100,start=1,sort='date'))
    request=Request(url,headers={'X-NCP-APIGW-API-KEY-ID':key_id,'X-NCP-APIGW-API-KEY':key})
    try:
        with urlopen(request,timeout=5) as response:raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('news_response_limit')
        payload=json.loads(raw);articles=[]
        for item in payload['items'][:100]:
            try:stamp=parsedate_to_datetime(item['pubDate']).isoformat()
            except (KeyError,ValueError,TypeError):continue
            articles.append(dict(title=clean_text(item.get('title')),description=clean_text(item.get('description')),
                url=item.get('originallink') or item.get('link'),source=item.get('publisher'),
                published_at=stamp,query=query,provider='NAVER_API_HUB',body_checked=False,
                attribution='related_news_only',published_time_basis='API_pubDate'))
        return dict(status='searched',query=query,response_count=len(payload['items']),raw_articles=articles,articles=relevant_articles(articles,sector,names,as_of))
    except Exception as error:
        # HTTP code/type only: headers, credentials and response body never logged.
        return dict(status='unavailable',query=query,articles=[],error_type=type(error).__name__,http_status=getattr(error,'code',None))


def search_company(name,as_of,*,refresh=False):
    query=query_for('',[name],as_of)
    records=search_records()
    cached=next((r for r in reversed(records) if r.get('query')==query
                 and r.get('analysis_at','')[:10]==as_of[:10]
                 and 'naver_status' in r and 'google_status' in r),None)
    if cached and not refresh:
        try:
            raw=cached.get('raw_articles',cached['articles'])
            cached_articles=relevant_articles(raw,'',[name],as_of)
            # Successful historic searches remain reusable; retry failures only
            # on explicit refresh or after a short cooldown.
            if (bool(cached_articles) and cached.get('naver_status')=='searched' and cached.get('google_status')=='searched') or (
                    datetime.now(timezone.utc)-instant(cached['searched_at'])<timedelta(minutes=5)):
                return dict(cached,analysis_at=as_of,articles=cached_articles,counts=article_counts(raw,name,as_of),cache_hit=True,request_performed=False)
        except (ValueError,KeyError,TypeError):pass
    # Both providers receive the exact same single-company query, once each.
    hub=search_hub('',[name],as_of)
    rss=search_public('',[name],as_of)
    raw=[dict(a,query=query) for a in hub.get('raw_articles',hub['articles'])]
    raw.extend(dict(a,provider='Google_News_RSS',query=query) for a in rss.get('raw_articles',rss['articles']))
    articles=relevant_articles(raw,'',[name],as_of)
    result=dict(naver_status=hub['status'],naver_error_type=hub.get('error_type'),naver_http_status=hub.get('http_status'),
        google_status=rss['status'],articles=articles,raw_articles=raw,
        naver_count=hub.get('response_count',len(hub['articles'])),google_count=rss.get('response_count',len(rss['articles'])),
        query=query,name=name,analysis_at=as_of,searched_at=datetime.now(timezone.utc).isoformat(),cache_hit=False,
        request_performed=True,counts=article_counts(raw,name,as_of))
    records=[r for r in records if (r.get('query'),r.get('analysis_at'))!=(query,as_of)]
    try:
        SEARCH_CACHE.parent.mkdir(parents=True,exist_ok=True)
        SEARCH_CACHE.write_text(json.dumps(dict(records=[*records,result]),ensure_ascii=False,indent=2),encoding='utf8')
    except OSError:result['cache_write_failed']=True
    return result


def search_news(sector,names,as_of,*,refresh=False):
    results=[search_company(name,as_of,refresh=refresh) for name in list(dict.fromkeys(names))[:3]]
    articles=relevant_articles([a for r in results for a in r['articles']],sector,names,as_of)
    return dict(sector=sector,analysis_at=as_of,articles=articles,companies=results,
                naver_status='searched' if results and all(r['naver_status']=='searched' for r in results) else 'unavailable',
                google_status='searched' if results and all(r['google_status']=='searched' for r in results) else 'unavailable',
                cache_hit=bool(results) and all(r['cache_hit'] for r in results))


def search_records():
    try:
        records=json.loads(SEARCH_CACHE.read_text(encoding='utf8')).get('records',[])
        return [r for r in records if isinstance(r,dict)] if isinstance(records,list) else []
    except (OSError,ValueError,TypeError,AttributeError):return []


def material_comment(article):
    """A short extract of the reported material, without a causal verdict."""
    text=clean_text(article.get('title',''))
    names=article.get('matched_names',[])
    if names and not any(name in text for name in names):
        # A passing mention in a broad headline must not borrow another company's material.
        sentences=re.split(r'[。!?]|(?<!\d)\.(?!\d)',clean_text(article.get('description','')))
        text=next((sentence for sentence in sentences if any(name in sentence for name in names)),text)
    text=re.sub(r'\s+-\s+'+re.escape(article.get('source') or '\0')+r'$','',text)
    # Keep a mixed headline's positive company clause, not another stock's decline.
    parts=[part.strip() for part in text.split(',')]
    if len(parts)>1 and any(name in parts[0] for name in names) and re.search(r'강세|상승|급등|수주|계약',parts[0]) and re.search(r'약세|하락|급락',','.join(parts[1:])):
        text=parts[0]
    text=re.sub(r'^\s*\[?특징주\]?\s*[,：:·-]?\s*','',text)
    text=text.replace('[특징주]','').strip()
    for name in names:
        text=re.sub(r'^'+re.escape(name)+r'\s*[,：:·-]?\s*','',text)
    clauses=[part.strip() for part in re.split('[…⋯]',text) if part.strip()]
    reported=next((part for part in clauses if re.search(r'기대|수혜|계약|발표|실적|정책|임상|허가|수주|관세|개발',part)),None)
    if reported:text=reported
    elif len(clauses)>1 and len(clauses[-1])>=8:text=clauses[-1]
    text=re.sub(r'\s*(?:앞두고\s*)?\d+(?:\.\d+)?%대?\s*(?:강세|급등|상승).*$', '',text).strip()
    text=re.sub(r'(?:에|로)?\s*[+-]?\d+(?:\.\d+)?%\s*[↑↓]?$','',text).strip().rstrip('↑↓')
    return text[:24]+'…' if len(text)>25 else text or '특징주 기사 내용 참고'


def company_summary(articles,name):
    related=[a for a in articles if name in a.get('matched_names',[])]
    related.sort(key=lambda a:instant(a['published_at']),reverse=True)
    chosen=next((a for a in related if name in a['title']),related[0] if related else None)
    return dict(summary=material_comment(chosen) if chosen else '관련 상승 재료 기사 없음',articles=related)


def common_articles(articles,leaders):
    names={s.get('name') for s in leaders};groups={}
    for article in sorted(articles,key=lambda a:instant(a['published_at']),reverse=True):
        direct={name for name in names if name and name in article.get('title','')}
        if not direct:continue
        comment=material_comment(article)
        entry=groups.setdefault(comment,dict(names=set(),articles=[]))
        entry['names'].update(direct.intersection(article.get('matched_names',[])))
        entry['articles'].append(article)
    # Articles are newest first; select the newest material mentioned for >=2 leaders.
    for entry in groups.values():
        if len(entry['names'])>=2:return entry['articles']
    return []


def search_snapshot(snapshot,*,refresh=False):
    """Explicit offline-dashboard preparation; never called by the renderer."""
    seen=set();results=[]
    for group in snapshot['analysis']['sectors'][:3]:
        leaders=[]
        for leader in group['leaders'][:3]:
            if leader['stock_code'] not in seen:
                seen.add(leader['stock_code']);leaders.append(leader)
        results.append(search_news(group['name'],[s['name'] for s in leaders],snapshot['reference_at'],refresh=refresh))
    return results

def summarize(articles,sector,leaders,as_of):
    accepted=[];rejected=[];cutoff=instant(as_of)
    for article in articles:
        reason=None
        try:
            published=instant(article['published_at'])
            if published>cutoff:reason='published_after_analysis'
            elif published<cutoff-timedelta(hours=WINDOW_HOURS):reason='published_before_3day_window'
            elif urlparse(article['url']).scheme not in ('http','https'):reason='invalid_public_url'
        except (ValueError,TypeError,KeyError):reason='publication_time_unverified'
        if reason:rejected.append(dict(article=article,reason=reason))
        else:accepted.append(article)
    accepted.sort(key=lambda a:instant(a['published_at']),reverse=True)
    return dict(analysis_at=as_of,articles=accepted,rejected=rejected)


def cached_summary(sector,leaders,as_of):
    try:articles=json.loads(CACHE.read_text(encoding='utf8'))['articles']
    except (OSError,ValueError,KeyError,TypeError):articles=[]
    articles=[a for a in articles if isinstance(a,dict)] if isinstance(articles,list) else []
    for record in search_records():
        extra=record.get('raw_articles',record.get('articles',[]))
        if isinstance(extra,list):
            articles.extend(a for a in extra if isinstance(a,dict))
    try:
        result=summarize(articles,sector,leaders,as_of)
        related=relevant_articles(result['articles'],sector,[s.get('name','') for s in leaders if s.get('name')],as_of)
        result['articles']=related
        result['companies']={s['stock_code']:company_summary(related,s.get('name','')) for s in leaders}
        result['common_articles']=common_articles(related,leaders)
        result['summary']=material_comment(result['common_articles'][0]) if result['common_articles'] else '공통 특징주 재료 없음'
        return result
    except (ValueError,KeyError,TypeError,AttributeError):
        return dict(summary='공통 특징주 재료 없음',articles=[],companies={},common_articles=[],rejected=[])


def main():
    global SEARCH_CACHE
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sector')
    p.add_argument('--name',action='append',default=[]);p.add_argument('--as-of')
    p.add_argument('--snapshot',type=Path);p.add_argument('--refresh',action='store_true')
    p.add_argument('--output',type=Path,default=SEARCH_CACHE)
    a=p.parse_args()
    SEARCH_CACHE=a.output
    if a.snapshot:
        from .c16_display import grouped_snapshot
        snapshot=json.loads(a.snapshot.read_text(encoding='utf8'))
        results=search_snapshot(grouped_snapshot(snapshot),refresh=a.refresh)
        print(json.dumps([dict(sector=r['sector'],naver_status=r['naver_status'],google_status=r['google_status'],
                              article_count=len(r['articles']),cache_hit=r['cache_hit']) for r in results],ensure_ascii=False))
        return
    if not a.sector or not a.as_of:p.error('--sector and --as-of, or --snapshot required')
    result=search_news(a.sector,a.name,a.as_of,refresh=a.refresh);result['sector']=a.sector
    print(json.dumps(dict(naver_status=result['naver_status'],google_status=result['google_status'],
                         article_count=len(result['articles']),cache_hit=result['cache_hit']),ensure_ascii=False))


if __name__=='__main__':main()
