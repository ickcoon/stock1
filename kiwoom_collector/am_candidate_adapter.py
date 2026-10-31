"""Offline C12 candidate join for existing 0198/0186 AM inputs."""
from copy import deepcopy
from collections import Counter
import re

from .equity_eligibility import apply_candidates, preview
from .master_data import instant


def normalize_code(value):
    if type(value) is int and 0 <= value <= 999999:
        return str(value).zfill(6)
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if re.fullmatch(r'[0-9]{1,6}', value):
        return value.zfill(6)
    return value if re.fullmatch(r'[0-9A-Z]{6}', value) else None


def join(document, classification, *, source_records=()):
    """Preserve observations; selection eligibility is a separate overlay."""
    cutoff = instant(document.get('selection_cutoff_at', document['trade_date']+'T09:50:00+09:00'))
    if document.get('market') != 'KRX':
        raise ValueError('candidate_join_requires_krx_scope')
    if instant(classification['at']) != cutoff:
        raise ValueError('candidate_master_cutoff_mismatch')
    if classification.get('synthetic') is not (document['source_kind'] == 'fixture'):
        raise ValueError('candidate_master_source_scope_mismatch')
    report = apply_candidates(classification)
    index = {}
    for row in report['records']:
        code = normalize_code(row['stock_code'])
        if code is None or code in index:
            raise ValueError('ambiguous_master_code')
        index[code] = row['candidate_eligibility']
    result = deepcopy(document)
    observed = []
    for snapshot in result['rankings']:
        codes = set()
        for row in snapshot['stocks']:
            code = normalize_code(row['stk_cd'])
            observed.append(dict(kind='ranking', code=code, original=deepcopy(row),
                                 observed_at=snapshot.get('observed_at')))
            if code is None or code in codes:
                raise ValueError('ambiguous_ranking_code_after_normalization')
            codes.add(code)
            row['stk_cd'] = code
    securities = {}
    for original, value in result.get('securities', {}).items():
        code = normalize_code(original)
        if code is None or code in securities:
            raise ValueError('ambiguous_security_code_after_normalization')
        securities[code] = value
    result['securities'] = securities
    for row in source_records:
        if row.get('kind') == 'turnover':
            observed.append(dict(kind='turnover', code=normalize_code(row.get('stock_code')),
                                 original=deepcopy(row)))
    codes = sorted({r['code'] for r in observed if r['code']} | set(securities))
    decisions = {c: deepcopy(index.get(c, dict(state='unmatched', confidence='pending',
                  reasons=['master_code_unmatched']))) for c in codes}
    diagnostics = []
    for row in observed:
        decision = decisions.get(row['code'], dict(state='unmatched', confidence='pending', reasons=['invalid_stock_code']))
        diagnostics.append(dict(row, decision=deepcopy(decision)))
    counts = Counter(analysis_candidate=0, confirmed_excluded=0, provisional_excluded=0, pending=0, unmatched=0)
    counts.update(d['state'] for d in decisions.values())
    groups = {state: [dict(stock_code=c, decision=d) for c, d in decisions.items() if d['state'] == state]
              for state in counts}
    reasons = {state: dict(Counter(reason for item in groups[state] for reason in item['decision']['reasons']))
               for state in counts}
    by_source = {}
    for kind in ('ranking', 'turnover'):
        unique = {r['code'] for r in observed if r['kind'] == kind and r['code']}
        by_source[kind] = dict(before_count=len(unique), after_count=sum(
            decisions[c]['state'] == 'analysis_candidate' for c in unique))
    result['candidate_filter'] = dict(version='c12-am-candidate-join-v1', at=cutoff.isoformat(),
        master_sha256=classification.get('provenance', {}).get('sha256'),
        master_hash_unchanged=classification.get('provenance', {}).get('hash_unchanged'),
        before_count=len(decisions), after_count=counts['analysis_candidate'], counts=dict(counts),
        groups=groups, reason_counts=reasons,
        by_source=by_source, decisions=decisions, observations=diagnostics,
        original_ranking_sources=[deepcopy(r) for r in source_records if r.get('kind') == 'ranking'],
        invalid_code_rows=sum(r['code'] is None for r in observed),
        absolute_turnover_floor_applied=False)
    return result


def enrich(document, path, *, synthetic, source_records=()):
    cutoff = document.get('selection_cutoff_at', document['trade_date']+'T09:50:00+09:00')
    return join(document, preview(path, at=cutoff, synthetic=synthetic), source_records=source_records)
