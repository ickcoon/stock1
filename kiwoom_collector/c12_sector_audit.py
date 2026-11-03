"""C12-AH offline legacy metadata join; never promotes candidate eligibility.

Reuse existing manual sector tags and taxonomy, not mock API observations.
No database creation, Store, authentication, transport or delivery is involved.
"""
import argparse
import json
from pathlib import Path

from .c12_latest import read_db, digest, observe, save_report
from .am_time_policy import instant
from .am_momentum import number
from .sector_taxonomy import link


def read_tags(path, *, as_of):
    at=instant(as_of)
    with read_db(path) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'stock_sectors','sectors','stock_sector_events'}<=tables:
            raise ValueError('legacy_sector_history_required')
        # Replay the existing history rather than applying a future edit or
        # untag retrospectively. Numeric event IDs deterministically order ties.
        rows=[dict(r) for r in db.execute('''SELECT e.event_id,e.stock_code,e.sector_id,
            e.action,e.source,e.changed_at,s.sector_name FROM stock_sector_events e
            JOIN sectors s ON s.sector_id=e.sector_id ORDER BY e.event_id LIMIT 10001''')]
        if len(rows)>10000:raise ValueError('sector_history_read_limit')
        states={}
        for row in rows:
            when=instant(row['changed_at'])
            if when>at:continue
            key=(row['stock_code'],row['sector_id'])
            if key not in states or (when,row['event_id'])>(instant(states[key]['changed_at']),states[key]['event_id']):
                states[key]=row
        active=[r for r in states.values() if r['action']=='tag']
        counts=dict(sectors=db.execute('SELECT COUNT(*) FROM sectors').fetchone()[0],
                    stored_tag_rows=db.execute('SELECT COUNT(*) FROM stock_sectors').fetchone()[0])
    return dict(tags=active,metadata_sha256=digest(active),counts=counts,
                source_database=str(Path(path).resolve()),
                source_kind='existing_manual_classification_metadata',as_of=at.isoformat())


def analyze(report, metadata):
    if instant(metadata['as_of'])!=instant(report['as_of']):raise ValueError('sector_as_of_mismatch')
    watchlist=report['observations']['watchlist']; codes={s['stock_code'] for s in watchlist}
    if len(codes)!=len(watchlist) or any(not isinstance(c,str) or len(c)!=6 for c in codes):
        raise ValueError('unique_string_stock_codes_required')
    tags={c:[] for c in codes}
    for row in metadata['tags']:
        if row['stock_code'] in codes:tags[row['stock_code']].append(row)
    securities={}; connections=[]
    for stock in watchlist:
        code=stock['stock_code'];raw=tags[code]
        coarse=sorted({link(r['sector_name'])['sector'] for r in raw if link(r['sector_name'])['sector']})
        # Exact coarse tag wins over supplementary fine tags. Multiple coarse
        # sectors are retained but no arbitrary representative is invented.
        state='mapped' if len(coarse)==1 else 'ambiguous' if coarse else 'fine_only' if raw else 'unclassified'
        securities[code]={'sectors':[{'name':name} for name in coarse]}
        connections.append(dict(stock_code=code,name=stock.get('name'),state=state,
            coarse_sectors=coarse,original_tags=raw,
            reported_change=stock.get('raw_change'),candidate_eligibility_promoted=False))
    observation=observe(report['snapshot_selection'],report['analysis']['rows'],securities)
    index={s['stock_code']:s for s in watchlist}
    sectors=observation['sectors']
    for group in sectors:
        members=[index[c] for c in group['stock_codes']]
        values=[number(s.get('raw_change')) for s in members]
        group.update(distinct_stocks=len(members),
            reported_positive_stock_codes=[s['stock_code'] for s,v in zip(members,values) if v is not None and v>0],
            reported_positive_count=sum(v is not None and v>0 for v in values),
            change_available_count=sum(v is not None for v in values),
            change_basis='stored_API_reported_unverified_market_time',
            watchlist_share_percent=round(100*len(members)/len(watchlist),4) if watchlist else 0,
            top20_share_percent=round(100*group['attention_count']/sum(s.get('ranking_rank') is not None for s in watchlist),4)
                if any(s.get('ranking_rank') is not None for s in watchlist) else None)
    rising=[s for s in sectors if s['reported_positive_count']>=2]
    rising.sort(key=lambda s:(-s['reported_positive_count'],-s['current_top5_count'],-s['attention_count'],s['name']))
    mapped=sum(c['state']=='mapped' for c in connections);any_tags=sum(bool(c['original_tags']) for c in connections)
    result=dict(schema_version='c12-ah-sector-audit-v1',as_of=report['as_of'],
        source_report_reference=report['evaluation_reference'],classification=metadata,
        coverage=dict(total=len(codes),with_existing_tag=any_tags,coarse_mapped=mapped,
            existing_tag_percent=round(100*any_tags/len(codes),4) if codes else 0,
            coarse_mapped_percent=round(100*mapped/len(codes),4) if codes else 0,
            ambiguous=sum(c['state']=='ambiguous' for c in connections)),
        connections=connections,unclassified_stock_codes=[c['stock_code'] for c in connections if c['state']!='mapped'],
        observation_sectors=sectors,attention_top3=sectors[:3],reported_rising_top3=rising[:3],
        rising_top3_status='available' if len(rising)>=3 else 'insufficient_corroborating_risers',
        sector_order_basis=observation['sector_order_basis'],
        candidate_selection_status=report['candidate_selection_status'],candidate_confirmed=False,
        telegram_send_enabled=False,metadata_only_join=True,
        limitations=['partial_mapping_coverage','classification_not_listing_or_capital_evidence',
                      'reported_change_not_verified_market_time','no_sector_inference_from_names_or_prefixes'])
    result['evaluation_reference']=digest(result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description='Read-only existing-sector audit; no collect or send')
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--sector-db',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    report=json.loads(a.report.read_text(encoding='utf-8'))
    result=analyze(report,read_tags(a.sector_db,as_of=report['as_of']))
    save_report(result,a.output)
    print(json.dumps(result['coverage']))


if __name__=='__main__':main()
