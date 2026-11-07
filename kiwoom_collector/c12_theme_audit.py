"""C12-AJ offline trading-theme observations from existing manual tag evidence."""
import argparse
import json
from decimal import Decimal
from pathlib import Path

from .c12_latest import read_db, digest
from .c12_sector_audit import read_tags
from .c12_master_audit import match
from .am_time_policy import instant
from .am_momentum import number


def analyze_themes(watchlist, tags, *, selected_themes=None, rising_policy=None):
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
    result = dict(themes=themes, attention_top3=themes[:3],
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
    if rising_policy is not None:
        result['rising_analysis'] = rising_analysis(stocks, themes, rising_policy)
    return result


def attention_component(stocks, memberships, weight):
    """Existing fractional rank contribution, also usable as attention-only evidence."""
    return sum((Decimal(21-s['ranking_rank'])/20*(Decimal(1)/memberships[s['stock_code']])
                for s in stocks if s.get('ranking_rank')), Decimal(0))*Decimal(str(weight))


def rising_analysis(stocks, themes, policy):
    """Optional C15-8 view over AJ's distinct-stock groups; legacy order intact.

    Breadth is the first ordering key. Weighted score is an initial hypothesis,
    not the C7 confirmed-candidate strategy. Unknown eligibility stays pending.
    """
    required = {'breadth', 'strength', 'attention', 'cross', 'change_cap_pct'}
    if set(policy) != required or any(number(str(v)) is None or Decimal(str(v)) <= 0 for v in policy.values()):
        raise ValueError('invalid_rising_policy')
    weights = {k: Decimal(str(v)) for k, v in policy.items()}
    memberships = {c: sum(c in t['stock_codes'] for t in themes) for c in stocks}
    ranked, held = [], []
    for theme in themes:
        members = [stocks[c] for c in theme['stock_codes']]
        rising = [s for s in members if number(s.get('raw_change')) is not None and number(s['raw_change']) > 0
                  and not s.get('explicitly_excluded') and s.get('capital_status') != 'below_minimum']
        if not rising:
            held.append(dict(name=theme['name'], reason='no_positive_observed_stocks',
                             observed_count=len(members), rising_count=0))
            continue
        # Split score contributions across memberships; do not inflate the
        # attention/strength of a stock solely because it carries more labels.
        part = lambda s: Decimal(1) / memberships[s['stock_code']]
        strength = sum((min(number(s['raw_change']), weights['change_cap_pct'])*part(s) for s in rising), Decimal(0))/len(rising)
        attention = attention_component(rising, memberships, 1)
        cross = sum((part(s) for s in rising if s.get('ranking_rank') and s.get('turnover_rank')), Decimal(0))
        components = dict(breadth=Decimal(len(rising))*weights['breadth'], strength=strength*weights['strength'],
                          attention=attention*weights['attention'], cross=cross*weights['cross'])
        def order(s):
            # Existing cap/type eligibility is retained as an independent state.
            return (s.get('eligibility_status') != 'eligible', -min(number(s['raw_change']), weights['change_cap_pct']),
                    s.get('ranking_rank') or 999, s.get('turnover_rank') or 999,
                    -Decimal(s.get('turnover_won') or '0'), s['stock_code'])
        leaders = sorted(rising, key=order)[:3]
        item = dict(name=theme['name'], rising_count=len(rising), observed_count=len(members),
                    score=str(sum(components.values())), components={k:str(v) for k,v in components.items()},
                    rising_stock_codes=sorted(s['stock_code'] for s in rising),
                    exclusive_rising_count=sum(memberships[s['stock_code']]==1 for s in rising),
                    multi_theme_stock_codes=sorted(s['stock_code'] for s in rising if memberships[s['stock_code']]>1),
                    confidence='low_single_stock' if len(rising)<2 else 'provisional_observation',
                    verified_turnover_won=str(sum((Decimal(s['turnover_won']) for s in rising if s.get('turnover_won')), Decimal(0))),
                    leaders=leaders, confirmed_leaders=[s for s in leaders if s.get('eligibility_status')=='eligible'],
                    candidate_confirmed=False)
        (ranked if len(rising)>=2 else held).append(item)
    ranked.sort(key=lambda t:(-t['rising_count'], -Decimal(t['score']), -Decimal(t['verified_turnover_won']), t['name']))
    for i, item in enumerate(ranked, 1): item['rank']=i
    return dict(sectors=ranked[:3], ranked_sectors=ranked, held_sectors=held, policy=policy,
                policy_status='initial_hypothesis', order_basis='distinct_rising_count_then_score',
                amount_score_applied=False, confirmed_sectors=[],
                overlap_policy='unique_per_theme_fractional_score_across_themes',
                exclusive_breadth_order=[t['name'] for t in sorted(ranked,key=lambda t:(-t['exclusive_rising_count'],-Decimal(t['score']),t['name']))][:3])


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
