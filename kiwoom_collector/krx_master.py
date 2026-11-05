"""C12-AO: offline KRX snapshot extension of the existing development master.

No HTTP, credentials, operational database writer or delivery. Theme edges are
historical external references, never automatically verified C12 membership.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import unicodedata

from . import master_data as master
from . import krx_openapi as krx
from . import external_theme_master as external
from .c12_external_theme_bridge import load as load_themes, label_view
from .c12_latest import read_db

VERSION = 'c12-ao-v1'
STATES = ('official_full_name', 'official_abbreviation', 'verified_alias',
          'verified_name_history', 'multiple_candidates', 'unmatched')
DDL = (
    '''CREATE TABLE ao_meta(version TEXT PRIMARY KEY, dataset_hash TEXT NOT NULL,
       basis_date TEXT NOT NULL, synthetic INTEGER NOT NULL, theme_sha256 TEXT NOT NULL,
       summary_json TEXT NOT NULL)''',
    '''CREATE TABLE krx_snapshots(service TEXT PRIMARY KEY, basis_date TEXT NOT NULL,
       source_kind TEXT NOT NULL, source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
       body_sha256 TEXT NOT NULL, requested_at TEXT NOT NULL, received_at TEXT NOT NULL,
       validation_status TEXT NOT NULL, row_count INTEGER NOT NULL)''',
    '''CREATE TABLE krx_securities(stock_code TEXT PRIMARY KEY REFERENCES stock_master(stock_code),
       basis_date TEXT NOT NULL, market TEXT NOT NULL, official_code_raw TEXT NOT NULL,
       standard_code TEXT NOT NULL UNIQUE, short_code_raw TEXT NOT NULL,
       official_name TEXT NOT NULL, abbreviated_name TEXT NOT NULL,
       security_group_raw TEXT NOT NULL, stock_type_raw TEXT NOT NULL, listing_date TEXT NOT NULL,
       product_type TEXT NOT NULL, type_verified INTEGER NOT NULL, classification_json TEXT NOT NULL,
       source_service TEXT NOT NULL REFERENCES krx_snapshots(service), collected_at TEXT NOT NULL,
       validation_status TEXT NOT NULL, raw_json TEXT NOT NULL)''',
    '''CREATE TABLE external_name_matches(raw_name TEXT PRIMARY KEY, basis_date TEXT NOT NULL,
       state TEXT NOT NULL, stock_code TEXT REFERENCES krx_securities(stock_code),
       official_name TEXT, market TEXT, confirmed INTEGER NOT NULL, review_required INTEGER NOT NULL,
       historical_identity_verified INTEGER NOT NULL, result_json TEXT NOT NULL)''',
    '''CREATE TABLE external_theme_relations(raw_name TEXT NOT NULL REFERENCES external_name_matches(raw_name),
       raw_theme TEXT NOT NULL, stock_code TEXT REFERENCES krx_securities(stock_code),
       source_json TEXT NOT NULL, PRIMARY KEY(raw_name,raw_theme))''',
    '''CREATE VIEW c12_krx_reference AS SELECT r.raw_name,r.raw_theme,r.stock_code,
       m.basis_date,m.historical_identity_verified,m.state,s.product_type
       FROM external_theme_relations r JOIN external_name_matches m USING(raw_name)
       LEFT JOIN krx_securities s ON r.stock_code=s.stock_code''',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized_name(value):
    # Preserve punctuation, case, corporate prefixes and preferred-share suffixes.
    # NFC + whitespace only: no prefix, edit-distance or substring matching.
    return ''.join(unicodedata.normalize('NFC', value).split())


def classify(record):
    group, kind = record['security_group_raw'], record['stock_type_raw']
    name = record['official_name']
    result = dict(product_type='UNKNOWN', type_verified=False,
                  share_class_raw=kind, security_group_raw=group,
                  basis='official_fields_insufficient', hint=None)
    explicit = {'부동산투자회사': 'REIT', 'ETF': 'ETF', '상장지수펀드': 'ETF',
                'ETN': 'ETN', '상장지수증권': 'ETN', '기업인수목적회사': 'SPAC'}
    if group in explicit:
        result.update(product_type=explicit[group], type_verified=True, basis='SECUGRP_NM')
    elif '기업인수목적' in name or '스팩' in record['abbreviated_name']:
        result.update(hint='SPAC', basis='official_name_hint_requires_product_review')
    elif group == '주권' and kind in ('우선주', '구형우선주', '신형우선주'):
        result.update(product_type='preferred_stock', type_verified=True,
                      basis='SECUGRP_NM+KIND_STKCERT_TP_NM')
    elif group == '주권' and kind == '보통주':
        result.update(product_type='common_stock', type_verified=True,
                      basis='SECUGRP_NM+KIND_STKCERT_TP_NM')
    # 종류주권 and foreign/depositary securities retain UNKNOWN product type.
    return result


def load_snapshot(path, *, service, basis_date, synthetic=False):
    document = json.loads(Path(path).read_text(encoding='utf8'))
    expected = 'synthetic_fixture' if synthetic else 'krx_live'
    if (service not in ('stk_isu_base_info', 'ksq_isu_base_info')
            or document.get('service') != service or document.get('source_kind') != expected
            or document.get('http_status') != 200 or document.get('basis_date') != basis_date):
        raise ValueError('snapshot_scope_or_origin_mismatch')
    started = master.instant(document['request_started_at'])
    received = master.instant(document['response_received_at'])
    if received < started or started.date() < date.fromisoformat(basis_date):
        raise ValueError('invalid_snapshot_clock')
    parsed = krx.parse(document['raw_response'], service=service,
                       bas_dd=basis_date.replace('-', ''), source_kind=expected)
    if not parsed['records']:
        raise ValueError('empty_official_master')
    provenance = dict(service=service, basis_date=basis_date, source_kind=expected,
                      source_path=str(Path(path).resolve()), source_sha256=sha(path),
                      body_sha256=master.digest(document['raw_response']),
                      requested_at=document['request_started_at'],
                      received_at=document['response_received_at'],
                      validation_status='response_schema_and_identity_uniqueness_checked',
                      row_count=parsed['row_count'])
    return parsed['records'], provenance


def match_names(names, records, *, basis_date, reviews=(), observation_dates=None):
    date.fromisoformat(basis_date)
    by_code = {master.code(r['stock_code']): r for r in records}
    if len(by_code) != len(records):
        raise ValueError('duplicate_short_code')
    indices = []
    for field, state in (('official_name', 'official_full_name'),
                         ('abbreviated_name', 'official_abbreviation')):
        exact, normalized = defaultdict(set), defaultdict(set)
        for r in records:
            if r[field]:
                exact[r[field]].add(r['stock_code'])
                normalized[normalized_name(r[field])].add(r['stock_code'])
        indices.append((field, state, exact, normalized))
    results = []
    for name in dict.fromkeys(names):
        candidates, state, evidence, normalization = set(), 'unmatched', [], 'none'
        # Union both official name namespaces before choosing a stage. A full
        # name equal to another security's abbreviation is ambiguous, not priority.
        exact_sets = [index[2].get(name, set()) for index in indices]
        candidates = set().union(*exact_sets)
        if candidates:
            state = 'official_full_name' if exact_sets[0] else 'official_abbreviation'
            evidence = [dict(field=i[0], value=name) for i, found in zip(indices, exact_sets) if found]
        else:
            norm = normalized_name(name)
            norm_sets = [i[3].get(norm, set()) for i in indices] if norm else [set(), set()]
            candidates = set().union(*norm_sets)
            if candidates:
                state = 'official_full_name' if norm_sets[0] else 'official_abbreviation'
                normalization = 'NFC_and_whitespace_only'
                evidence = [dict(field=i[0], normalized_value=norm)
                            for i, found in zip(indices, norm_sets) if found]
        if not candidates:
            for review in reviews:
                if review.get('raw_name') != name or review.get('verified') is not True:
                    continue
                target = by_code.get(review.get('stock_code'))
                if (not target or review.get('kind') not in ('alias', 'name_history')
                        or not review.get('evidence_url', '').startswith('https://')
                        or review.get('canonical_name') != target['official_name']
                        or review.get('market') != target['market']):
                    continue
                # Historical-name evidence must cover every observed source date.
                days = (observation_dates or {}).get(name, []) if review['kind'] == 'name_history' else [basis_date]
                if not days or not review.get('valid_from') or not review.get('reviewed_at'):
                    continue
                try:
                    start = date.fromisoformat(review['valid_from'])
                    end = date.fromisoformat(review['valid_to']) if review.get('valid_to') else None
                    reviewed = master.instant(review['reviewed_at']).date()
                    valid = all(start <= date.fromisoformat(d) and
                                (end is None or date.fromisoformat(d) < end) for d in days)
                    valid = valid and reviewed >= start
                except (ValueError, TypeError):
                    continue
                if valid:
                    candidates.add(target['stock_code'])
                    evidence.append(dict(review))
            if candidates:
                state = ('verified_name_history' if any(e.get('kind') == 'name_history' for e in evidence)
                         else 'verified_alias')
        if len(candidates) > 1:
            state = 'multiple_candidates'
        chosen = by_code[next(iter(candidates))] if len(candidates) == 1 else None
        results.append(dict(raw_name=name, basis_date=basis_date, state=state,
                            stock_code=chosen['stock_code'] if chosen else None,
                            official_name=chosen['official_name'] if chosen else None,
                            market=chosen['market'] if chosen else None,
                            confirmed=chosen is not None, review_required=chosen is None,
                            confidence='snapshot_name_identity' if chosen else 'unresolved',
                            historical_identity_verified=False,
                            historical_review_required=True,
                            normalization=normalization, evidence=evidence,
                            candidates=[dict(stock_code=c, official_name=by_code[c]['official_name'],
                                             market=by_code[c]['market']) for c in sorted(candidates)],
                            reason=None if chosen else 'ambiguous_names' if candidates else
                            'no_exact_official_name_or_dated_verified_alias'))
    return results


def statistics(records, matches, relations):
    matched = {m['raw_name']: m for m in matches}
    confirmed = sum(m['confirmed'] for m in matches)
    connected = sum(bool(matched[r['stock_name_raw']]['confirmed']) for r in relations)
    kinds = Counter(classify(r)['product_type'] for r in records)
    alpha = [r for r in records if not r['stock_code'].isdigit()]
    return dict(master_stocks=len(records), names=len(matches), confirmed_names=confirmed,
                confirmed_name_percent=round(100 * confirmed / len(matches), 4) if matches else 0,
                states={s: sum(m['state'] == s for m in matches) for s in STATES},
                relations=len(relations), confirmed_relations=connected,
                confirmed_relation_percent=round(100 * connected / len(relations), 4) if relations else 0,
                distinct_confirmed_codes=len({m['stock_code'] for m in matches if m['confirmed']}),
                multi_theme_names=sum(n > 1 for n in Counter(r['stock_name_raw'] for r in relations).values()),
                product_types=dict(kinds), alphanumeric_codes=len(alpha),
                alphanumeric_official_types=[dict(security_group_raw=g, stock_type_raw=t, count=n)
                    for (g, t), n in sorted(Counter((r['security_group_raw'], r['stock_type_raw']) for r in alpha).items())],
                spac_hints=sum(classify(r)['hint'] == 'SPAC' for r in records),
                duplicate_short_codes=len(records)-len({r['stock_code'] for r in records}),
                duplicate_standard_codes=len(records)-len({r['standard_code'] for r in records}),
                duplicate_official_names=[name for name, n in Counter(r['official_name'] for r in records).items() if n > 1],
                duplicate_abbreviations=[name for name, n in Counter(r['abbreviated_name'] for r in records).items() if n > 1])


def publish(name, records, provenance, loaded, inspection, *, basis_date, reviews=(), synthetic=False):
    """Create one immutable, idempotent development snapshot using C10's writer."""
    names = [r['stock_name_raw'] for r in loaded['metadata']['master_stock_names']]
    relations = loaded['metadata']['master_stock_theme']
    if len(names) != len(set(names)):
        raise ValueError('duplicate_raw_master_names')
    if len({(r['stock_name_raw'], r['theme_label']) for r in relations}) != len(relations):
        raise ValueError('duplicate_raw_relations')
    if any(r['stock_name_raw'] not in set(names) for r in relations):
        raise ValueError('orphan_external_name')
    if len({r['standard_code'] for r in records}) != len(records):
        raise ValueError('duplicate_standard_code')
    if ({p['service'] for p in provenance} != {'stk_isu_base_info', 'ksq_isu_base_info'}
            or len(provenance) != 2 or any(p['basis_date'] != basis_date for p in provenance)
            or any(r['basis_date'] != basis_date for r in records)
            or any(r['source_kind'] != ('synthetic_fixture' if synthetic else 'krx_live') for r in records)):
        raise ValueError('two_market_same_date_origin_required')
    dates = defaultdict(list)
    for r in loaded['records']:
        dates[r['raw_stock_name']].append(r['trade_date'])
    matches = match_names(names, records, basis_date=basis_date, reviews=reviews, observation_dates=dates)
    summary = statistics(records, matches, relations)
    summary.update(basis_date=basis_date, historical_membership_verified=False,
                   c12_47_comparison=dict(state='not_reproducible_missing_AK_and_observation_inputs',
                                         reported_baseline=24, reported_total=47,
                                         new_confirmed=None, after=None),
                   actual_api_calls=0, operational_writes=0)
    stable_provenance = sorted([{k: p[k] for k in ('service', 'basis_date', 'source_kind')}
                                for p in provenance], key=lambda p: p['service'])
    identity = master.digest(dict(version=VERSION, records=sorted(records, key=lambda r: r['stock_code']),
                                  provenance=stable_provenance,
                                  theme_sha256=inspection['sha256'], matches=sorted(matches, key=lambda m: m['raw_name']),
                                  relations=sorted(relations, key=lambda r: (r['stock_name_raw'], r['theme_label'])),
                                  synthetic=synthetic))
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('invalid_master_scope')
    path = master.writable_path(master.MASTER_ROOT / (name + '.db'))
    if path.exists():
        with read_db(path) as db:
            row = db.execute('SELECT dataset_hash,summary_json FROM ao_meta WHERE version=?', (VERSION,)).fetchone()
            if row is None or row['dataset_hash'] != identity:
                raise ValueError('existing_snapshot_differs_use_new_name')
            return dict(state='already_imported', path=str(path), summary=json.loads(row['summary_json']))
    at = datetime.now(timezone.utc).isoformat()
    path = master.create_master(name, synthetic=synthetic, at=at)
    try:
        with master.MasterStore(path) as store:
            for statement in DDL:
                store.db.execute(statement)
            store.db.executemany('INSERT INTO krx_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)',
                [tuple(p[k] for k in ('service', 'basis_date', 'source_kind', 'source_path', 'source_sha256',
                                     'body_sha256', 'requested_at', 'received_at', 'validation_status', 'row_count'))
                 for p in provenance])
            for r in records:
                source = next(p for p in provenance if krx.SERVICES[p['service']][0] == r['market'])
                # Do not promote a historical snapshot into today's live C12 gate.
                store.put_stock(r['stock_code'], r['abbreviated_name'] or r['official_name'],
                                r['market'], 'unknown', source='KRX:' + source['service'],
                                updated_at=source['received_at'], registered_at=at, verified=False)
                classification = classify(r)
                store.db.execute('INSERT INTO krx_securities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (r['stock_code'], basis_date, r['market'], r['raw']['ISU_CD'], r['standard_code'],
                     r['raw']['ISU_SRT_CD'], r['official_name'], r['abbreviated_name'],
                     r['security_group_raw'], r['stock_type_raw'], r['listing_date'],
                     classification['product_type'], int(classification['type_verified']),
                     json.dumps(classification, ensure_ascii=False), source['service'], source['received_at'],
                     'official_snapshot_schema_validated_not_current_eligibility', json.dumps(r['raw'], ensure_ascii=False)))
            for m in matches:
                store.db.execute('INSERT INTO external_name_matches VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (m['raw_name'], basis_date, m['state'], m['stock_code'], m['official_name'], m['market'],
                     int(m['confirmed']), int(m['review_required']), 0, json.dumps(m, ensure_ascii=False)))
            by_name = {m['raw_name']: m for m in matches}
            for r in relations:
                store.db.execute('INSERT INTO external_theme_relations VALUES(?,?,?,?)',
                    (r['stock_name_raw'], r['theme_label'], by_name[r['stock_name_raw']]['stock_code'],
                     json.dumps(r, ensure_ascii=False)))
            store.db.execute('INSERT INTO ao_meta VALUES(?,?,?,?,?,?)',
                (VERSION, identity, basis_date, int(synthetic), inspection['sha256'], json.dumps(summary, ensure_ascii=False)))
    except BaseException:
        # This path was exclusively created above, never an existing user's DB.
        path.unlink(missing_ok=True)
        raise
    return dict(state='created', path=str(path), summary=summary)


def export_review(path, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with read_db(path) as db:
        summary = json.loads(db.execute('SELECT summary_json FROM ao_meta').fetchone()[0])
        matches = [json.loads(r[0]) for r in db.execute('SELECT result_json FROM external_name_matches ORDER BY raw_name')]
        securities = [dict(r) for r in db.execute('SELECT * FROM krx_securities ORDER BY market,stock_code')]
        relations = [dict(r) for r in db.execute('SELECT * FROM c12_krx_reference ORDER BY raw_name,raw_theme')]
    groups = {'matches': matches, 'confirmed': [m for m in matches if m['confirmed']],
              'review-required': [m for m in matches if m['review_required']],
              'alphanumeric-codes': [r for r in securities if not r['stock_code'].isdigit()],
              'securities': securities, 'relations': relations}
    for name, rows in groups.items():
        (output / (name + '.json')).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf8')
        if rows:
            with (output / (name + '.csv')).open('w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                for row in rows:
                    values = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
                    # Neutralize spreadsheet formulas without changing JSON or DB originals.
                    writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v
                                     for k, v in values.items()})
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    columns = ('raw_name', 'state', 'stock_code', 'official_name', 'market', 'normalization', 'review_required')
    header = ''.join('<th>' + html.escape(c) + '</th>' for c in columns)
    body = ''.join('<tr>' + ''.join('<td>' + html.escape(str(m[c] if m[c] is not None else '')) + '</td>'
                                  for c in columns) + '</tr>' for m in matches)
    # label_view reuses the existing bridge's display rules; raw labels stay intact.
    labels = [label_view(s) for s in sorted({r['raw_theme'] for r in relations})]
    (output / 'theme-label-review.json').write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding='utf8')
    document = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>C12-AO 검토</title>'
                '<style>body{font:15px sans-serif;margin:28px}table{border-collapse:collapse}th,td{padding:7px;'
                'border:1px solid #ccc;text-align:left}th{position:sticky;top:0;background:#eef}</style>'
                '<h1>C12-AO 공식 스냅샷 이름 매칭</h1><p>기준일 ' + html.escape(summary['basis_date']) +
                ' · 이름 확정 ' + str(summary['confirmed_names']) + '/' + str(summary['names']) +
                ' · 과거 테마 소속 및 실전 적격성 검증 아님</p><p>47종목 AK 입력 부재: 재현 불가</p>'
                '<table><thead><tr>' + header + '</tr></thead><tbody>' + body + '</tbody></table></html>')
    (output / 'review.html').write_text(document, encoding='utf8')
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kospi', type=Path, required=True)
    p.add_argument('--kosdaq', type=Path, required=True)
    p.add_argument('--theme-db', type=Path, required=True)
    p.add_argument('--basis-date', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--reviews', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--synthetic', action='store_true')
    a = p.parse_args(argv)
    # Review exports are confined to this checkout's ignored diagnostics directory.
    output = Path(a.output).absolute()
    allowed = master.PROJECT / 'diagnostics'
    if output.resolve() != output or not output.is_relative_to(allowed):
        raise ValueError('development_output_required')
    inspection = external.inspect(a.theme_db, full_integrity=True)
    if (inspection.get('integrity_check') != ['ok'] or inspection['foreign_key_violations']
            or not inspection['hash_unchanged']):
        raise ValueError('source_theme_integrity_failed')
    loaded = load_themes(a.theme_db)
    if loaded['logical_orphans']:
        raise ValueError('source_theme_orphan_relations')
    records, provenance = [], []
    for source, service in ((a.kospi, 'stk_isu_base_info'), (a.kosdaq, 'ksq_isu_base_info')):
        rows, origin = load_snapshot(source, service=service, basis_date=a.basis_date, synthetic=a.synthetic)
        records.extend(rows)
        provenance.append(origin)
    reviews = json.loads(a.reviews.read_text(encoding='utf8')) if a.reviews else []
    result = publish(a.name, records, provenance, loaded, inspection,
                     basis_date=a.basis_date, reviews=reviews, synthetic=a.synthetic)
    summary = export_review(result['path'], output)
    if sha(a.theme_db) != inspection['sha256']:
        raise ValueError('source_theme_changed')
    print(json.dumps(dict(state=result['state'], database=result['path'], summary=summary), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
