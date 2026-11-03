"""Offline search/review journal overlay on existing STOCK1 tags. No transport/DB writes.

Search providers are injected by callers. Without one, record unavailable, never
fabricate results. CLI plans queries or imports actual externally checked evidence.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from .am_candidate_adapter import normalize_code
from .am_time_policy import instant
from .c12_latest import digest
from .sectors import label
from .c12_theme_audit import analyze_themes
from .theme_normalization import normalize

ROOT=Path(__file__).resolve().parents[3]/'diagnostics'/'theme_review'
ZERO='0'*64


def journal_path(path):
    # Collapse ordinary '..' segments without following filesystem symlinks.
    path=Path(os.path.abspath(path))
    if path.suffix!='.jsonl' or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('development_theme_journal_required')
    if path.resolve()!=path or ROOT.resolve()!=ROOT.absolute():
        raise ValueError('symlink_journal_forbidden')
    return path


def read_events(path):
    path=journal_path(path)
    if not path.exists():return []
    events=[];previous=ZERO
    for line in path.read_text(encoding='utf8').splitlines():
        event=json.loads(line);value={k:v for k,v in event.items() if k!='hash'}
        if event['previous']!=previous or event['hash']!=digest(value) or event['seq']!=len(events)+1:
            raise ValueError('journal_chain_invalid')
        events.append(event);previous=event['hash']
    return events


def append(path,kind,payload,*,at,actor):
    path=journal_path(path);events=read_events(path)
    event=dict(seq=len(events)+1,previous=events[-1]['hash'] if events else ZERO,
               kind=kind,payload=payload,at=instant(at).isoformat(),actor=label(actor,'actor'))
    if events and instant(event['at'])<instant(events[-1]['at']):raise ValueError('journal_time_reversal')
    event['hash']=digest(event)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Single-writer journal; exclusive lock prevents concurrent lost updates.
    lock=path.with_suffix('.lock')
    owned_lock=False
    try:
        with lock.open('x'):
            owned_lock=True
            if read_events(path)!=events:raise ValueError('journal_changed_retry')
            with path.open('a',encoding='utf8',newline='\n') as f:
                f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
    finally:
        # Windows cannot unlink an open file handle.
        # Do not remove a lock owned by a competing writer.
        if owned_lock:lock.unlink()
    return event


def state(base,events,*,at):
    themes={t['sector_name'] for t in base['classification_source']['tags']}
    aliases={};proposals={};reviews={};searches={}
    for e in events:
        if instant(e['at'])>instant(at):continue
        p=e['payload'];kind=e['kind']
        if kind=='theme_create':themes.add(p['name'])
        elif kind in ('theme_rename','theme_merge'):
            aliases[p['old']]=p['new'];themes.discard(p['old']);themes.add(p['new'])
        elif kind=='proposal':proposals[e['hash']]=dict(p,registered_at=e['at'])
        elif kind=='review':reviews[p['proposal_id']]=dict(p,reviewed_at=e['at'])
        elif kind=='search':searches[p['stock_code']]=p
    def canonical(name):
        seen=set()
        while name in aliases:
            if name in seen:raise ValueError('theme_alias_cycle')
            seen.add(name);name=aliases[name]
        return name
    for name in aliases:canonical(name)
    return dict(themes=themes,canonical=canonical,proposals=proposals,reviews=reviews,searches=searches)


def plan(base,events,*,at):
    s=state(base,events,at=at)
    verified={p['stock_code'] for key,p in s['proposals'].items()
              if s['reviews'].get(key,{}).get('status')=='confirmed'}
    result=[]
    for row in base['connections']:
        if row['themes'] or row['stock_code'] in verified:continue
        result.append(dict(stock_code=row['stock_code'],stock_name=row['name'],
            query=f'"{row["stock_code"]}" "{row["name"]}" 테마 관련주',
            prior_search=s['searches'].get(row['stock_code'])))
    return result


def search_pending(base,path,*,at,provider=None):
    """Provider accepts a query, returns real evidence refs/status; no default network."""
    for item in plan(base,read_events(path),at=at):
        result=provider(item['query']) if provider else dict(status='unavailable',evidence_urls=[])
        if result.get('status') not in ('completed','failed','unavailable'):raise ValueError('search_status_required')
        append(path,'search',dict(item,**result),at=at,actor='search_provider')


def theme_event(base,path,kind,*,at,actor,reason,name=None,old=None,new=None):
    if kind not in ('theme_create','theme_rename','theme_merge'):raise ValueError('invalid_theme_event')
    s=state(base,read_events(path),at=at)
    payload=dict(reason=label(reason,'reason'))
    if kind=='theme_create':
        name=s['canonical'](label(name,'theme'))
        if name in s['themes']:return None
        payload['name']=name
    else:
        old,new=label(old,'old'),label(new,'new')
        if old not in s['themes'] or old==new or s['canonical'](new)==old:raise ValueError('invalid_theme_change')
        if kind=='theme_merge' and new not in s['themes']:raise ValueError('merge_target_missing')
        if kind=='theme_rename' and new in s['themes']:raise ValueError('use_merge_for_existing_target')
        payload.update(old=old,new=new)
    return append(path,kind,payload,at=at,actor=actor)


def propose(base,path,record,*,at,actor):
    code=normalize_code(record['stock_code'])
    pending={r['stock_code']:r for r in plan(base,read_events(path),at=at)}
    if code not in pending or record['stock_name']!=pending[code]['stock_name']:
        raise ValueError('unclassified_identity_required')
    record=dict(record,stock_code=code,theme=label(record['theme'],'theme'))
    s=state(base,read_events(path),at=at)
    record['theme']=s['canonical'](record['theme'])
    if record['theme'] not in s['themes']:raise ValueError('register_or_reuse_theme_first')
    for evidence in record['evidence']:
        url=urlsplit(evidence['url'])
        if url.scheme not in ('http','https') or not url.netloc or url.username or url.password:
            raise ValueError('evidence_url_required')
        if evidence['access'] not in ('full_page','search_snippet','fetch_failed'):raise ValueError('evidence_access_required')
        datetime.fromisoformat(evidence['checked_date'])
        if evidence['checked_date']>instant(at).date().isoformat():raise ValueError('future_evidence')
        if not evidence.get('note'):raise ValueError('evidence_note_required')
    record.setdefault('conflict',False)
    return append(path,'proposal',record,at=at,actor=actor)


def review(base,path,proposal_id,*,status,at,actor,reason):
    if status not in ('confirmed','needs_review','rejected'):raise ValueError('invalid_review_status')
    s=state(base,read_events(path),at=at);p=s['proposals'][proposal_id]
    if status=='confirmed':
        sources={urlsplit(e['url']).hostname.removeprefix('www.') for e in p['evidence']
                 if e['access']=='full_page' and e.get('identity_checked') and e.get('theme_supported')
                 and e.get('basis') in ('market_theme','business_corroboration')}
        market=any(e['access']=='full_page' and e.get('basis')=='market_theme'
                   and e.get('theme_supported') and e.get('identity_checked') for e in p['evidence'])
        if p['conflict'] or len(sources)<2 or not market:raise ValueError('insufficient_review_evidence')
    return append(path,'review',dict(proposal_id=proposal_id,status=status,reason=label(reason,'reason')),
                  at=at,actor=actor)


def report(base,events,*,at,watchlist):
    s=state(base,events,at=at);rows=[];tags=[]
    for row in base['connections']:
        code=row['stock_code'];confirmed=[];pending=[]
        for key,p in s['proposals'].items():
            if p['stock_code']!=code:continue
            review=s['reviews'].get(key,{})
            status=review.get('status','needs_review')
            target=confirmed if status=='confirmed' else pending if status=='needs_review' else None
            if target is not None:target.append(dict(p,proposal_id=key,review=review,canonical_theme=s['canonical'](p['theme'])))
        names=sorted({s['canonical'](t) for t in row['themes']}|{p['canonical_theme'] for p in confirmed})
        status='confirmed' if names else 'needs_review' if pending else 'unclassified'
        rows.append(dict(stock_code=code,name=row['name'],status=status,themes=names,
                         confirmed_web_mappings=confirmed,pending_web_mappings=pending,
                         search=s['searches'].get(code),legacy_mapping_preserved=bool(row['themes']),
                         classification_reason='legacy_or_reviewed_theme' if names else
                            'additional_theme_evidence_review_required' if pending else 'no_confirmed_trading_theme'))
        tags.extend(dict(stock_code=code,sector_name=t,source='legacy_or_reviewed_web_overlay') for t in names)
    counts=Counter(r['status'] for r in rows)
    result=dict(schema_version='c12-aj-theme-research-v1',checked_at=at,observation_as_of=base['as_of'],
        retrospective_enrichment=True,historical_candidate_decisions_unchanged=True,
        counts={k:counts[k] for k in ('confirmed','needs_review','unclassified')},
        initial_searchable_count=sum(not r['themes'] for r in base['connections']),
        searched_count=sum(r['search'] is not None and r['search']['status']=='completed' for r in rows),
        existing_mapped_count=sum(bool(r['themes']) for r in base['connections']),
        newly_confirmed_count=sum(bool(r['confirmed_web_mappings']) and not r['legacy_mapping_preserved'] for r in rows),
        mapping_percent=round(100*counts['confirmed']/len(rows),4),connections=rows,
        theme_observations=analyze_themes(watchlist,tags),
        pending_search_count=len(plan(base,events,at=at)),actual_kiwoom_api_calls=0,telegram_sent=0)
    result['theme_normalization']=normalize(result,watchlist)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,required=True);p.add_argument('--journal',type=Path,required=True)
    p.add_argument('--at',required=True)
    p.add_argument('--action',choices=('plan','search','import','review','theme','report'),default='plan')
    p.add_argument('--input',type=Path);p.add_argument('--watchlist-report',type=Path)
    a=p.parse_args();base=json.loads(a.base.read_text(encoding='utf8'))
    if a.action=='plan':result=plan(base,read_events(a.journal),at=a.at)
    elif a.action=='search':
        search_pending(base,a.journal,at=a.at)
        result=dict(status='unavailable',reason='no_python_web_search_provider_configured')
    elif a.action=='import':
        result=propose(base,a.journal,json.loads(a.input.read_text(encoding='utf8')),at=a.at,actor='local_researcher')
    elif a.action=='review':
        result=review(base,a.journal,at=a.at,**json.loads(a.input.read_text(encoding='utf8')))
    elif a.action=='theme':
        result=theme_event(base,a.journal,at=a.at,**json.loads(a.input.read_text(encoding='utf8')))
    else:
        observations=json.loads(a.watchlist_report.read_text(encoding='utf8'))['observations']['watchlist']
        result=report(base,read_events(a.journal),at=a.at,watchlist=observations)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
