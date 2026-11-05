"""C12-AJ offline trading-theme observations from existing manual tag evidence."""
import argparse
import json
from pathlib import Path

from .c12_latest import read_db, digest
from .c12_sector_audit import read_tags
from .c12_master_audit import match
from .am_time_policy import instant
from .am_momentum import number


def analyze_themes(watchlist, tags, *, selected_themes=None):
    """Exact labels, no industrial hierarchy or inferred stock assignments.

    Each code counts once per theme, while cross-theme overlap is intentional.
    Selecting a detail theme never requires reporting or rolling up its parent.
    """
    stocks = {s['stock_code']: s for s in watchlist}
    if len(stocks) != len(watchlist):
        raise ValueError('duplicate_watchlist_code')
    groups = {}
    for tag in tags:
        code, name = tag['stock_code'], tag['sector_name']
        if code not in stocks or not tag.get('source') or not name.strip():
            continue
        if selected_themes is not None and name not in selected_themes:
            continue
        groups.setdefault(name, {}).setdefault(code, []).append(tag)
    themes = []
    for name, members in groups.items():
        observations = [stocks[c] for c in members]
        positive = [s['stock_code'] for s in observations
                    if number(s.get('raw_change')) is not None and number(s['raw_change']) > 0]
        themes.append(dict(name=name, stock_codes=sorted(members), distinct_stocks=len(members),
            evidence=members,
            current_top5_count=sum(s.get('ranking_rank') is not None and s['ranking_rank']<=5 for s in observations),
            attention_count=sum(s.get('ranking_rank') is not None for s in observations),
            turnover_top40_count=sum(s.get('turnover_rank') is not None for s in observations),
            reported_positive_count=len(positive),reported_positive_stock_codes=sorted(positive),
            change_available_count=sum(number(s.get('raw_change')) is not None for s in observations),
            simultaneous_rise_status='reported_multiple_risers' if len(positive)>=2 else 'insufficient_multiple_risers',
            leadership_status='unconfirmed',
            leadership_hold_reasons=['market_time_unverified','turnover_amount_unit_unverified']+
                ([] if len(positive)>=2 else ['fewer_than_two_reported_risers']),
            raw_turnover_by_stock={s['stock_code']:s.get('raw_turnover') for s in observations},
            candidate_confirmed=False))
    themes.sort(key=lambda t:(-t['current_top5_count'],-t['attention_count'],-t['turnover_top40_count'],t['name']))
    for rank, theme in enumerate(themes,1):
        theme['attention_rank']=rank
    linked={c for t in themes for c in t['stock_codes']}
    return dict(themes=themes, attention_top3=themes[:3],
        connections=[dict(stock_code=c,name=s.get('name'),
                          themes=[t['name'] for t in themes if c in t['stock_codes']],
                          state='linked' if c in linked else 'unclassified')
                     for c,s in stocks.items()],
        coverage=dict(total=len(stocks),theme_linked=len(linked),unclassified=len(stocks)-len(linked),
                      theme_linked_percent=round(100*len(linked)/len(stocks),4) if stocks else 0),
        unclassified_stock_codes=sorted(set(stocks)-linked),
        confirmed_leading_themes=[],trade_eligible_ranking=[],
        attention_order_basis='current_top5_count_then_attention_count_then_turnover_top40_count',
        theme_count_sum_is_not_unique_stock_total=True,
        selected_themes=sorted(selected_themes) if selected_themes is not None else None)


def audit(report, legacy, *, selected_themes=None):
    at=report['as_of']
    metadata=read_tags(legacy,as_of=at)
    with read_db(legacy) as db:
        master=[dict(r) for r in db.execute('SELECT stock_code,stock_name,updated_at FROM stocks')]
    watchlist=report['observations']['watchlist']
    matches={s['stock_code']:match(s,master) for s in watchlist}
    allowed={c for c,m in matches.items() if m['state']=='code' and instant(m['record']['updated_at'])<=instant(at)}
    tags=[t for t in metadata['tags'] if t['stock_code'] in allowed]
    result=dict(schema_version='c12-aj-trading-theme-audit-v1',as_of=at,
        source_report_reference=report['evaluation_reference'],classification_source=metadata,
        **analyze_themes(watchlist,tags,selected_themes=selected_themes),
        actual_api_calls=0,telegram_sent=0,candidate_confirmed=False,
        classification_policy='existing_manual_exact_labels_no_hierarchy_no_industry_inference',
        limitations=['partial_existing_tag_coverage','manual_tags_not_current_theme_validation',
                     'legacy_names_have_no_history','market_time_unverified','capital_and_eligibility_not_promoted'])
    result['evaluation_reference']=digest(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('report','legacy','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--theme',action='append',help='Optional exact theme labels, independently selected')
    a=p.parse_args()
    result=audit(json.loads(a.report.read_text(encoding='utf8')),a.legacy,
                 selected_themes=set(a.theme) if a.theme is not None else None)
    with a.output.open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result['coverage']))


if __name__=='__main__':
    main()
