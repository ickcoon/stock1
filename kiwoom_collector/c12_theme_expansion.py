"""C12-AK bounded offline research priority and before/after comparisons."""
from copy import deepcopy

from .am_time_policy import instant
from .am_momentum import number
from .theme_normalization import normalize


def prioritize(baseline,observation,*,limit=15):
    if type(limit) is not int or not 10<=limit<=15:raise ValueError('research_limit_10_to_15')
    at=instant(observation['as_of'])
    stocks={s['stock_code']:s for s in observation['observations']['watchlist']}
    if len(stocks)!=len(observation['observations']['watchlist']):raise ValueError('duplicate_observation_code')
    history={s['stock_code']:s for s in observation['analysis']['rows']}
    normalized=normalize(baseline,list(stocks.values()))
    confirmed={s['stock_code']:set(s['themes']) for s in normalized['connections']}
    rows=[]
    for row in baseline['connections']:
        if row['themes']:continue
        code=row['stock_code'];stock=stocks[code]
        ranks=[];slots=set()
        for r in history.get(code,{}).get('rank_history',[]):
            if (r.get('top20_complete') is not True or instant(r['received_at'])>at
                    or instant(r['observed_at'])>at):continue
            if r['slot'] in slots:raise ValueError('duplicate_history_slot')
            slots.add(r['slot'])
            # A complete TOP20 snapshot explicitly records absence as rank=None.
            if r['rank'] is None:continue
            if type(r['rank']) is not int or not 1<=r['rank']<=20:raise ValueError('invalid_history_rank')
            ranks.append(r['rank'])
        candidates={p['theme'] for p in row.get('pending_web_mappings',[])}
        companions=sorted(c for c,names in confirmed.items() if c!=code and names&candidates)
        rows.append(dict(stock_code=code,name=row['name'],previous_status=row['status'],
            top5_appearances=sum(r<=5 for r in ranks),top20_appearances=len(ranks),
            current_ranking_rank=stock.get('ranking_rank'),best_observed_rank=min(ranks) if ranks else None,
            turnover_rank=stock.get('turnover_rank'),turnover_amount_used=False,
            turnover_top10=stock.get('turnover_rank') is not None and stock['turnover_rank']<=10,
            candidate_themes=sorted(candidates),confirmed_candidate_theme_companions=companions,
            companion_basis='exact_pending_label_overlap_with_preexisting_confirmed_mapping_not_proof_of_membership',
            reported_change=stock.get('raw_change'),change_basis='stored_API_reported_market_time_unverified'))
    def key(row):
        change=number(row['reported_change'])
        return (-row['top5_appearances'],-row['top20_appearances'],row['current_ranking_rank'] or 999,
                row['best_observed_rank'] or 999,row['turnover_rank'] or 999,
                -len(row['confirmed_candidate_theme_companions']),change is None,-change if change is not None else 0,
                row['stock_code'])
    rows.sort(key=key)
    for i,row in enumerate(rows,1):
        row.update(research_priority=i,selected=i<=limit)
    return dict(limit=limit,unmapped_count=len(rows),selected=rows[:limit],all_unmapped=rows,
        ordering='top5_frequency_then_top20_frequency_then_current_and_best_rank_then_turnover_rank_then_known_companions_then_reported_change',
        scope='stored_0198_0920_0930_0940_0950_and_selected_0186_top40',
        weighted_score_used=False,turnover_absolute_amount_used=False)


def attention_view(normalized,*,prefer_parents=True):
    """Prefer a known parent only when it covers the entire detail member set.

    All detail observations remain available in the original normalized output.
    This is a report selection, not a taxonomy expansion or leadership decision.
    """
    themes=normalized['observations']['themes']
    members={t['name']:set(t['stock_codes']) for t in themes}
    hidden=set()
    if prefer_parents:
        for row in normalized['connections']:
            for link in row['links']:
                detail,parent=link['detail'],link['parent']
                if parent and members.get(detail,set())<=members.get(parent,set()):hidden.add(detail)
    rows=[deepcopy(t) for t in themes if t['name'] not in hidden]
    for i,row in enumerate(rows,1):row['display_attention_rank']=i
    return dict(ranking=rows,top3=rows[:3],suppressed_detail_labels=sorted(hidden),
                basis=normalized['observations']['attention_order_basis'],
                leadership_confirmed=False,detail_only_reporting_available=True)


def compare(before,after,observation,priority):
    watchlist=observation['observations']['watchlist']
    before_norm=normalize(before,watchlist);after_norm=after['theme_normalization']
    old={r['stock_code']:r for r in before['connections']}
    added=[dict(stock_code=r['stock_code'],name=r['name'],themes=r['themes'])
           for r in after['connections'] if r['themes'] and not old[r['stock_code']]['themes']]
    preserved=all(set(old[r['stock_code']]['themes'])<=set(r['themes']) for r in after['connections'])
    return dict(schema_version='c12-ak-theme-expansion-v1',as_of=observation['as_of'],checked_at=after['checked_at'],
        retrospective_enrichment=True,research_priority=priority,
        before=dict(counts=before['counts'],mapped=before['counts']['confirmed'],
                    mapping_percent=before['mapping_percent'],unmapped=len(watchlist)-before['counts']['confirmed'],
                    attention=attention_view(before_norm),normalization=before_norm),
        after=dict(counts=after['counts'],mapped=after['counts']['confirmed'],mapping_percent=after['mapping_percent'],
                   unmapped=len(watchlist)-after['counts']['confirmed'],attention=attention_view(after_norm)),
        newly_mapped=added,newly_mapped_count=len(added),existing_mapping_preserved=preserved,
        deferred=[dict(stock_code=r['stock_code'],name=r['name'],status=r['status'],
                       proposed_themes=sorted({p['theme'] for p in r['pending_web_mappings']}))
                  for r in after['connections'] if not r['themes']],
        full_report=after,confirmed_leading_themes=[],trade_eligible_ranking=[],
        actual_kiwoom_api_calls=0,telegram_sent=0,
        limitations=['partial_mapping_coverage','retrospective_classification_not_historical_decision',
                     'unverified_market_time_and_turnover_amount_units','theme_membership_not_intraday_leadership'])
