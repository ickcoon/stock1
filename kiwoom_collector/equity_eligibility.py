"""Offline C12 policy overlay. Never edits classifications or publishes analysis."""
import argparse
from collections import Counter
import json
import re

from .master_data import digest, instant

from .classification_preview import preview
from .stock_catalog import aggregate_turnover, SECTORS

VERSION = 'c12-equity-eligibility-v1'
EXCLUDED_TYPES = frozenset(('ETF', 'ETN', 'REIT', 'SPAC', 'mutual_fund', 'infrastructure_product'))

PRODUCT_PAIRS = {
    ('8', 'ETF'): 'ETF', ('60', 'ETN'): 'ETN', ('70', 'ETN(손실제한)'): 'ETN',
    ('90', 'ETN(변동성)'): 'ETN', ('6', '리츠'): 'REIT',
    ('2', '인프라투자금융'): 'infrastructure_product', ('4', '뮤추얼펀드'): 'mutual_fund',
}
CANDIDATE_VERSION = 'c12-practical-candidates-v1'


def candidate_decision(record, *, at, include_preferred=False):
    """Exploration scope only: candidate never implies verified common stock."""
    if type(include_preferred) is not bool:
        raise ValueError('include_preferred_must_be_boolean')
    cutoff = instant(at)
    evidence = record.get('product_source') or {}
    raw = evidence.get('raw') or {}
    result = dict(confidence='pending', reasons=[], evidence=evidence,
                  raw_sha256=digest(raw), rule_version=CANDIDATE_VERSION)

    def finish(state, confidence, reason):
        return dict(result, state=state, confidence=confidence, reasons=[reason])

    if (record.get('excluded_from_counts') or record.get('stock_state') == 'conflict'
            or raw.get('code') != record['stock_code'] or not evidence.get('run_id')
            or evidence.get('source') != 'ka10099'
            or record.get('data_origin') not in ('api_response', 'synthetic_fixture')):
        return finish('pending', 'pending', 'source_or_identity_unverified')
    try:
        if not instant(evidence['updated_at']) <= instant(evidence['applied_at']) <= cutoff:
            return finish('pending', 'pending', 'source_after_cutoff')
    except (ValueError, KeyError, TypeError):
        return finish('pending', 'pending', 'source_time_unverified')
    if record.get('catalog_present') is not True:
        return finish('pending', 'pending', 'catalog_missing_review')
    pair = (raw.get('marketCode'), raw.get('marketName'))
    kind = PRODUCT_PAIRS.get(pair)
    company = raw.get('companyClassName', '')
    hint = record.get('security_type', 'unknown')
    verified = record.get('type_verified') is True and bool((record.get('type_evidence') or '').strip())
    if (raw.get('marketCode') != record.get('market_code')
            or raw.get('name') != record.get('stock_name')
            or (kind and hint not in ('unknown', kind))
            or (kind and company in ('스팩', '기업인수목적회사'))
            or (verified and kind and hint != kind)
            or (verified and company in ('스팩', '기업인수목적회사') and hint != 'SPAC')
            or (raw.get('orderWarning') == '1' and pair != ('8', 'ETF'))):
        return finish('pending', 'pending', 'product_evidence_conflict')
    if verified and hint in EXCLUDED_TYPES:
        return finish('confirmed_excluded', 'verified', 'excluded_type:' + hint)
    if verified and hint == 'preferred_stock' and not include_preferred:
        return finish('confirmed_excluded', 'verified', 'preferred_stock_disabled')
    if raw.get('orderWarning') == '1' and pair == ('8', 'ETF'):
        return finish('confirmed_excluded', 'verified', 'documented_etf_warning')
    if kind:
        return finish('provisional_excluded', 'provisional', 'raw_product_pair:' + kind)
    # An unrecognized code/name pairing must never fall through to candidates.
    expected = {'0': ('거래소', 'KOSPI'), '10': ('코스닥', 'KOSDAQ')}
    market = expected.get(pair[0])
    if not market or pair[1] != market[0] or record.get('market') != market[1]:
        return finish('pending', 'pending', 'market_pair_unresolved')
    if company in ('스팩', '기업인수목적회사') or hint == 'SPAC':
        return finish('pending', 'pending', 'spac_review_needed')
    if (company in ('ETF', 'ETN', '리츠', 'REIT', '뮤추얼펀드', '인프라투자금융')
            or re.search(r'스팩|ETF|ETN|리츠$', raw.get('name', ''), re.IGNORECASE)):
        return finish('pending', 'pending', 'product_label_review_needed')
    # Names are only review triggers, never type verification or exclusion proof.
    if (hint == 'preferred_stock' or re.search(r'(?:우[BC]?|우선주)(?:\(전환\))?$', raw.get('name', ''))):
        if not (verified and hint == 'preferred_stock' and include_preferred):
            return finish('pending', 'pending', 'preferred_review_needed')
    if hint not in ('common_stock', 'preferred_stock', 'unknown'):
        return finish('pending', 'pending', 'unresolved_type_hint:' + hint)
    if (record.get('identity_verified') is not True or record.get('listing_status') != 'listed'):
        return finish('pending', 'pending', 'listing_unverified_or_inactive')
    return finish('analysis_candidate', 'provisional', 'known_market_no_product_clue')


def apply_candidates(classification, *, include_preferred=False):
    """Retain strict eligibility and all evidence alongside the practical decision."""
    report = apply_policy(classification, include_preferred=include_preferred)
    records = [dict(r, candidate_eligibility=candidate_decision(r, at=report['at'],
               include_preferred=include_preferred)) for r in report['records']]
    counts = Counter(confirmed_excluded=0, provisional_excluded=0, analysis_candidate=0, pending=0)
    reasons = {k: Counter() for k in counts}
    for row in records:
        decision = row['candidate_eligibility']
        counts[decision['state']] += 1
        reasons[decision['state']].update(decision['reasons'])
    return dict(report, records=records, candidate_policy=dict(version=CANDIDATE_VERSION,
        include_preferred=include_preferred, before_count=len(records),
        after_count=counts['analysis_candidate'], counts=dict(counts),
        reason_counts={k: dict(v) for k, v in reasons.items()},
        candidate_is_verified_common_stock=False))


def decide(record, *, include_preferred=False):
    """Type and market remain independent; unverified type always stays pending."""
    if type(include_preferred) is not bool:
        raise ValueError('include_preferred_must_be_boolean')
    kind = record.get('security_type', 'unknown')
    reasons = []
    if record.get('excluded_from_counts') or record.get('stock_state') == 'conflict':
        reasons.append('identity_unresolved')
    if record.get('type_verified') is not True or not record.get('type_evidence', '').strip():
        reasons.append('security_type_unverified')
    elif kind in ('unknown', 'other') or kind not in EXCLUDED_TYPES | {'common_stock', 'preferred_stock'}:
        reasons.append('security_type_unresolved')
    # No unverified hint or name can cause a definitive product exclusion.
    if reasons:
        return dict(state='pending', reasons=reasons)
    if kind in EXCLUDED_TYPES:
        return dict(state='excluded', reasons=['excluded_type:' + kind])
    if kind == 'preferred_stock' and not include_preferred:
        return dict(state='excluded', reasons=['preferred_stock_disabled'])
    if record.get('market') not in ('KOSPI', 'KOSDAQ'):
        reasons.append('market_unresolved')
    if record.get('catalog_present') is not True:
        reasons.append('catalog_presence_unverified')
    if record.get('data_origin') not in ('api_response', 'synthetic_fixture'):
        reasons.append('origin_unverified')
    if record.get('identity_verified') is not True or record.get('listing_status') != 'listed':
        reasons.append('listing_unverified_or_inactive')
    return dict(state='pending' if reasons else 'included', reasons=reasons or ['verified_equity'])


def apply_policy(classification, *, include_preferred=False):
    """Retain the full C12 source records and attach a separate policy decision."""
    records = []
    seen = set()
    for original in classification['records']:
        code = original['stock_code']
        if code in seen:
            raise ValueError('duplicate_classification_code')
        seen.add(code)
        records.append(dict(original, eligibility=decide(original, include_preferred=include_preferred)))
    counts = Counter(included=0, excluded=0, pending=0)
    reasons = {state: Counter() for state in counts}
    for row in records:
        decision = row['eligibility']
        counts[decision['state']] += 1
        reasons[decision['state']].update(decision['reasons'])
    return dict(classification, records=records, eligibility_policy=dict(version=VERSION,
        include_preferred=include_preferred, before_count=len(records), after_count=counts['included'],
        counts=dict(counts), reason_counts={k: dict(v) for k, v in reasons.items()},
        reason_counts_may_overlap=True))


def sector_turnover(observations, classification, *, include_preferred=False):
    """Opt-in 15-sector path: eligible equities and confirmed representatives only.

    Reuse the C11 duplicate-observation rejection and single-primary summation.
    Compute eligibility here, never trust a caller-supplied eligibility field.
    """
    report = apply_policy(classification, include_preferred=include_preferred)
    assignments = []
    held = {}
    for row in report['records']:
        decision = row['eligibility']
        code = row['stock_code']
        if decision['state'] != 'included':
            held[code] = decision
        elif (row.get('sector_state') != 'confirmed' or row.get('duplicate_primary')
              or row.get('representative_sector') not in SECTORS):
            held[code] = dict(state='pending', reasons=['sector_unconfirmed_or_conflicting'])
        else:
            assignments.append(dict(stock_code=code, sector_id=row['representative_sector'],
                                    kind='industry', is_primary=True, verified=True))
    result = aggregate_turnover(observations, assignments)
    for code in result['excluded']:
        result['excluded'][code] = held.get(code, dict(state='pending', reasons=['classification_missing']))
    result['eligibility_policy'] = report['eligibility_policy']
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--at', required=True)
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--include-preferred', action='store_true')
    parser.add_argument('--candidates', action='store_true', help='Separate provisional exploration scope')
    args = parser.parse_args(argv)
    policy = apply_candidates if args.candidates else apply_policy
    report = policy(preview(args.db, at=args.at, synthetic=args.synthetic),
                          include_preferred=args.include_preferred)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
