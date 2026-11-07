"""Display-only grouping of existing positive C15 candidates; no score changes."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

ALIASES={'신재생에너지':'신재생에너지','태양광/신재생':'신재생에너지',
         '반도체':'반도체/MLCC','MLCC':'반도체/MLCC','MLCC(적층세라믹콘덴서)':'반도체/MLCC'}


def grouped_snapshot(snapshot):
    policy=json.loads((Path(__file__).resolve().parents[1]/'config/c16-display-classification.json').read_text(encoding='utf8'))
    groups={};measurements=snapshot['diagnostics']['stock_measurements']
    for sector in snapshot['analysis'].get('ranked_sectors',snapshot['analysis']['sectors']):
        name=ALIASES.get(sector['name'],sector['name'])
        # Whitespace variants are typographic duplicates, not another theme.
        key=''.join(name.split())
        group=groups.setdefault(key,dict(name=name,source_names=[],original_c15_ranks=[],rising_stock_codes=set(),leaders=[]))
        group['source_names'].append(sector['name']);group['original_c15_ranks'].append(sector['rank'])
        group['rising_stock_codes'].update(sector['rising_stock_codes'])
        for leader in sector['leaders']:
            if not any(s['stock_code']==leader['stock_code'] for s in group['leaders']):group['leaders'].append(deepcopy(leader))
    # Primary display ownership only. Every original theme membership remains.
    corporate=[g for g in groups.values() if len(g['rising_stock_codes'])>=policy['minimum_anchor_members'] and
               any(''.join(n.split()).endswith(policy['corporate_label_suffix']) for n in g['source_names'])]
    industries=[g for g in groups.values() if g not in corporate and g['name'] in policy['industry_anchor_labels']
                and len(g['rising_stock_codes'])>=policy['minimum_anchor_members']]
    anchored=set();owners={}
    for group in corporate+industries:
        for code in sorted(group['rising_stock_codes']):
            owners.setdefault(code,group['name']);anchored.add(code)
    values=[];held=[]
    for group in groups.values():
        original_codes=sorted(group['rising_stock_codes'])
        is_anchor=group in corporate+industries
        codes=[c for c in original_codes if owners.get(c)==group['name']] if is_anchor else [c for c in original_codes if c not in anchored]
        # Retained themes also get exclusive primary ownership, never duplicate
        # count/amount merely because a stock has multiple historical labels.
        if not is_anchor:
            codes=[c for c in codes if c not in owners]
            if len(codes)>=policy['minimum_independent_theme_members']:
                for code in codes:owners[code]=group['name']
        known=[]
        for code in codes:
            value=measurements.get(code,{}).get('turnover',{}).get('eok')
            if value is not None:known.append(Decimal(value))
        group.update(original_rising_stock_codes=original_codes,independent_stock_codes=codes,
                     classification_basis='observed_corporate_or_industry_anchor' if is_anchor else 'independent_observed_members_only',
                     rising_stock_codes=codes,rising_count=len(codes),leaders=[s for s in group['leaders'] if s['stock_code'] in codes][:3],
                     turnover_eok=str(sum(known,Decimal(0))) if known else None,turnover_known_count=len(known),
                     amount_scope='observed_positive_candidates_partial_sum_not_inflow_or_market_total')
        display_key=''.join(group['name'].split())
        group['name']=policy['display_labels'].get(display_key,group['name'])
        note=next((n for n in policy['external_catalyst_notes'] if n['date']==snapshot.get('reference_at','')[:10] and n['observed_sector']==group['name']),None)
        group['observed_sector']=group['name']
        group['catalyst']=deepcopy(note) if note else dict(description=None,status='상승 재료 미확인',verified=False)
        from .c16_news import cached_summary
        group['news']=cached_summary(group['name'],group['leaders'],snapshot['reference_at'])
        group['representative_metrics']={s['stock_code']:deepcopy(measurements.get(s['stock_code'],{})) for s in group['leaders']}
        if len(codes)<policy['minimum_independent_theme_members']:
            group['hold_reason']='independent_theme_members_below_minimum';held.append(group)
        else:values.append(group)
    values.sort(key=lambda g:(g['turnover_eok'] is None,-Decimal(g['turnover_eok'] or 0),min(g['original_c15_ranks'])))
    for rank,group in enumerate(values,1):group['rank']=rank
    result=deepcopy(snapshot)
    result['display_ranking']=dict(rule='unique_positive_candidate_observed_turnover_partial_sum_descending',
        groups=values,held_groups=held,classification_policy=policy,
        original_C15_TOP3=[s['name'] for s in snapshot['analysis']['sectors']],internal_analysis_unchanged=True,
        primary_display_ownership={c:policy['display_labels'].get(''.join(name.split()),name) for c,name in owners.items()})
    result['analysis']['sectors']=values[:3]
    return result
