"""Offline, read-only C10/C11 classification inventory; never publishes reviews."""
import argparse
from collections import Counter
import json
import sqlite3

from . import master_data as master, stock_catalog as catalog
from . import sector_taxonomy as taxonomy


def preview(path, *, at, synthetic=False):
    at = master.instant(at).isoformat()
    markets = Counter({name: 0 for name in ('KOSPI', 'KOSDAQ', 'UNKNOWN')})
    types = {name: Counter(verified=0, unverified=0) for name in
             ('common_stock', 'ETF', 'ETN', 'REIT', 'SPAC', 'preferred_stock', 'other', 'unknown')}
    sectors = {name: Counter(confirmed=0, proposed=0) for name in catalog.SECTORS}
    states = Counter(confirmed=0, proposed=0, unclassified=0, conflict=0)
    records = []
    with master.read_master(path, synthetic=synthetic) as (reader, provenance):
        db = reader.db
        for item in db.execute('SELECT stock_code FROM stock_master ORDER BY stock_code'):
            code = item['stock_code']
            stock = reader.stock(code, at=at)
            info = reader.catalog_info(code, at=at)
            if 'stock_name' not in stock:
                # No unique historical identity at the requested cutoff.
                records.append(dict(stock_code=code, stock_state=stock['state'], excluded_from_counts=True))
                continue
            market = info['market'] if info else stock['market']
            markets[market] += 1
            kind = info['security_type'] if info else 'unknown'
            verified = bool(info and info['type_verified'])
            types.setdefault(kind, Counter(verified=0, unverified=0))['verified' if verified else 'unverified'] += 1
            latest = {}
            for row in db.execute('''SELECT t.*,s.sector_name,s.kind,s.active FROM stock_sector t
                JOIN sector_master s ON s.sector_id=t.sector_id
                WHERE t.stock_code=? AND t.registered_at<=? AND s.updated_at<=?
                ORDER BY t.registered_at,t.id''', (code, at, at)):
                latest[row['sector_id']] = dict(row)
            tags = [r for r in latest.values() if r['active'] and r['status'] != 'rejected'
                    and r['valid_from'] <= at and (r['valid_to'] is None or r['valid_to'] > at)]
            industries = [r for r in tags if r['kind'] == 'industry' and r['status'] == 'verified'
                          and r['evidence'].strip() and r['source'].strip()]
            primaries = [r for r in industries if r['is_primary']]
            chosen = primaries[0] if len(primaries) == 1 else industries[0] if len(industries) == 1 else None
            candidates = sorted({taxonomy.link(r['sector_name'])['sector'] for r in tags if r['kind'] == 'industry'
                                 and r['status'] == 'proposed' and taxonomy.link(r['sector_name'])['sector']})
            state, representative = 'unclassified', None
            # Inspect sector evidence independently of market/type activation gates.
            newer_manual = [r for r in industries if info and r['registered_at'] > info['applied_at']
                            and not r['source'].startswith('ka10099_mapping:')]
            if newer_manual:
                manual_primary = [r for r in newer_manual if r['is_primary']]
                chosen = (manual_primary[0] if len(manual_primary) == 1 else
                          newer_manual[0] if len(newer_manual) == 1 else None)
            if info and not newer_manual:
                mapping = info['mapping_state']
                if mapping in ('mapped', 'preserved_manual'):
                    chosen = next((r for r in industries if r['sector_id'] == info['representative_sector_id']), None)
                    state = 'confirmed' if chosen and chosen['sector_name'] in sectors else 'conflict'
                elif mapping == 'review_needed':
                    state = 'proposed' if len(candidates) == 1 else 'conflict' if candidates else 'unclassified'
                elif mapping != 'unclassified':
                    state = 'conflict'
            elif industries:
                state = ('confirmed' if taxonomy.link(chosen['sector_name'])['sector'] else 'unclassified') if chosen else 'conflict'
            elif candidates:
                state = 'proposed' if len(candidates) == 1 else 'conflict'
            if len(primaries) > 1:
                state = 'conflict'
            if state == 'confirmed':
                representative = taxonomy.link(chosen['sector_name'])['sector']
            elif state == 'proposed':
                representative = candidates[0]
            if representative:
                sectors[representative][state] += 1
            states[state] += 1
            records.append(dict(stock_code=code, stock_name=stock['stock_name'], market=market,
                market_code=info['market_code'] if info else None, security_type=kind, type_verified=verified,
                type_evidence=info['type_evidence'] if info else None,
                catalog_present=bool(info['present']) if info else None,
                sector_state=state, representative_sector=representative, proposed_sectors=candidates,
                sector_bridge_version=taxonomy.VERSION,
                sector_links=[dict(original_sector=r['sector_name'], **taxonomy.link(r['sector_name']))
                              for r in tags if r['kind'] == 'industry'],
                duplicate_primary=len(primaries) > 1, tag_count=len(tags),
                tags=[dict(sector=r['sector_name'], kind=r['kind'], status=r['status'],
                           is_primary=bool(r['is_primary']), evidence=r['evidence'], source=r['source'],
                           source_updated_at=r['source_updated_at'], registered_at=r['registered_at'],
                           valid_from=r['valid_from'], valid_to=r['valid_to'],
                           source_sha256=r['source_sha256'], record_hash=r['record_hash']) for r in tags],
                stock_state=stock['state'], analysis_classification_state=reader.classification(code, at=at)['state']))
        stored = [r[0] for r in db.execute("SELECT sector_name FROM sector_master WHERE kind='industry' ORDER BY sector_name")]
    return dict(schema_version='classification-preview-v1', at=at, synthetic=synthetic,
        actual_api_calls=0, database_updated=False, provenance=provenance,
        universe='stock_master identities available at cutoff; includes catalog present=0',
        unique_stocks=sum(markets.values()), excluded_identity_count=sum(r.get('excluded_from_counts', False) for r in records),
        markets=dict(markets), product_types={k: dict(v) for k, v in types.items()},
        sector_states=dict(states), representative_sectors={k: dict(v) for k, v in sectors.items()},
        duplicate_primary_count=sum(r.get('duplicate_primary', False) for r in records),
        multiple_tags_count=sum(r.get('tag_count', 0) > 1 for r in records),
        taxonomy=dict(c10=list(master.BROAD_SECTORS), c11=list(catalog.SECTORS), stored=stored,
            c10_only=sorted(set(master.BROAD_SECTORS)-set(catalog.SECTORS)),
            c11_only=sorted(set(catalog.SECTORS)-set(master.BROAD_SECTORS))), records=records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--at', required=True, help='Timezone-aware classification cutoff')
    parser.add_argument('--synthetic', action='store_true', help='Require a synthetic master')
    args = parser.parse_args(argv)
    try:
        result = preview(args.db, at=args.at, synthetic=args.synthetic)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps(dict(error=str(error), database_updated=False, actual_api_calls=0), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
