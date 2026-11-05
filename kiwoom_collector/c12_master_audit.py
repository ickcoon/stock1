"""C12-AI explicit offline metadata join. No collector integration or DB writes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from .c12_latest import read_db, digest
from .c12_sector_audit import analyze, read_tags
from .am_time_policy import instant
from .equity_eligibility import decide


def match(stock, rows):
    """Exact code first. Name fallback never overrides a valid conflicting code."""
    code = stock['stock_code']
    exact = [r for r in rows if r['stock_code'] == code]
    if exact:
        if len(exact) != 1 or exact[0]['stock_name'] != stock['name']:
            return dict(state='conflict', record=None)
        return dict(state='code', record=exact[0])
    names = [r for r in rows if r['stock_name'] == stock['name']]
    if names:
        # A well-formed observation code cannot be replaced by a different code.
        if re.fullmatch(r'[0-9A-Z]{6}', code or ''):
            return dict(state='name_code_conflict', record=None)
        if len(names) == 1:
            return dict(state='unique_name', record=names[0])
        return dict(state='ambiguous_name', record=None)
    return dict(state='unmatched', record=None)


def audit(report, legacy, cap_path):
    at = report['as_of']
    with read_db(legacy) as db:
        rows = [dict(r) for r in db.execute('SELECT stock_code,stock_name,updated_at FROM stocks')]
    # Current legacy names lack history: retain this limitation explicitly.
    metadata = read_tags(legacy, as_of=at)
    allowed = {s['stock_code'] for s in report['observations']['watchlist']
               if match(s, rows)['state'] in ('code', 'unique_name')}
    metadata['tags'] = [r for r in metadata['tags'] if r['stock_code'] in allowed]
    metadata['metadata_sha256'] = digest(metadata['tags'])
    base = analyze(report, metadata)
    with read_db(cap_path) as db:
        counts = {t: db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
                  for t in ('stock_master','stock_history','stock_sector','market_cap_cache')}
    if any(counts.values()):
        raise ValueError('nonempty_c10_requires_full_historical_cache_review')
    connections = []
    for stock, sector in zip(report['observations']['watchlist'], base['connections']):
        identity = match(stock, rows)
        record = identity['record']
        if record and instant(record['updated_at']) > instant(at):
            identity = dict(state='after_cutoff', record=record)
        eligibility = decide(dict(security_type='unknown', type_verified=False))
        connections.append(dict(sector, master_match=identity, eligibility=eligibility,
            capital=dict(state='missing', source=str(Path(cap_path).resolve()),
                         observed_at=None, value_won=None),
            hold_reasons=['security_type_unverified','listing_and_market_unverified',
                          'verified_real_capital_missing','original_candidate_policy_held']))
    matches = Counter(r['master_match']['state'] for r in connections)
    result = dict(schema_version='c12-ai-master-audit-v1', as_of=at,
        source_report_reference=report['evaluation_reference'],
        legacy_source=str(Path(legacy).resolve()), legacy_stock_count=len(rows),
        legacy_names_metadata_sha256=digest(rows), c10_counts=counts,
        coverage=dict(base['coverage'], identity_match_counts=dict(matches),
                      identity_matched=matches['code']+matches['unique_name'],
                      identity_match_percent=round(100*(matches['code']+matches['unique_name'])/len(connections),4),
                      capital_verified=0,capital_unknown=len(connections),eligibility_decidable=0),
        connections=connections,attention_top3=base['attention_top3'],
        observation_sectors=base['observation_sectors'],trade_eligible_ranking=[],
        candidate_confirmed_count=0,actual_api_calls=0,telegram_sent=0,
        limitations=['legacy_names_have_no_historical_identity_proof',
                     'legacy_stock_rows_are_not_verified_kospi_kosdaq_catalog',
                     'partial_classification','stored_changes_have_unverified_market_time'])
    result['evaluation_reference']=digest(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('report','legacy','c10','output'):
        p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args()
    result=audit(json.loads(a.report.read_text(encoding='utf8')),a.legacy,a.c10)
    # Output is a new JSON artifact; never allow replacing a source or existing file.
    with a.output.open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result['coverage'],ensure_ascii=True))


if __name__=='__main__':
    main()
