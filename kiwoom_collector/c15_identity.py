"""C15-9 offline identity evidence. No source writer, transport or tracking engine."""
import json
import re

from .am_time_policy import instant
from .c12_latest import read_db
from .krx_openapi import STANDARD_CODE, decimal_value

CODE = re.compile(r'[0-9A-Z]{6}', re.ASCII)
MARKETS = {'KOSPI', 'KOSDAQ'}


def exact_code(value, official=(), market=None):
    """Six characters, including alphanumeric codes; never guess market/digits.

    Kiwoom's seven-character A prefix is accepted only with an official target.
    Six-character A-leading symbols are preserved. Venue suffixes are rejected
    for the current KRX-only request profile, rather than silently stripped.
    """
    if not isinstance(value, str): return None
    if CODE.fullmatch(value):
        code = value
    elif len(value)==7 and value.startswith('A') and CODE.fullmatch(value[1:]):
        code = value[1:]
        if not any(r['stock_code']==code for r in official): return None
    else:
        return None
    if market is not None and not any(r['stock_code']==code and r['market']==market for r in official):
        return None
    return code


def load_official(daily_db, checkpoint_db, *, as_of):
    """Reuse stored dated KRX master joins and their original receipt evidence.

    Raw master receipts may have moved. Only hash-bound, checked daily identity
    rows can supply the official KRX daily name here, never invented full names.
    Both the master receipt and the daily receipt must predate the cutoff.
    """
    at = instant(as_of)
    evidence = None
    rejected = []
    with read_db(checkpoint_db) as db:
        for row in db.execute('SELECT day,details_json FROM dates WHERE day<=? ORDER BY day DESC LIMIT 120', (at.date().isoformat(),)):
            detail = json.loads(row['details_json'])
            entries = {m:detail.get('markets', {}).get(m, {}).get('master', {}) for m in MARKETS}
            try:
                good = all(e['basis_date']==row['day'] and e['source_kind']=='krx_live'
                           and e['service']==('stk_isu_base_info' if m=='KOSPI' else 'ksq_isu_base_info')
                           and e['validation_status']=='response_schema_and_identity_uniqueness_checked'
                           and instant(e['requested_at'])<=instant(e['received_at'])<=at for m,e in entries.items())
            except (KeyError, TypeError, ValueError):
                good = False
            if good:
                evidence = dict(basis_date=row['day'], markets=entries)
                break
            rejected.append(row['day'])
    if evidence is None:
        return [], dict(status='unavailable_at_cutoff', rejected_basis_dates=rejected)
    records, skipped = [], 0
    with read_db(daily_db) as db:
        rows = db.execute('SELECT * FROM daily_latest WHERE day=?', (evidence['basis_date'],)).fetchall()
        raw_capitals={}
        identifiers=sorted({r['snapshot_id'] for r in rows if 'snapshot_id' in r.keys()})
        if identifiers and db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_rows'").fetchone():
            for raw in db.execute('SELECT snapshot_id,raw_json FROM raw_rows WHERE snapshot_id IN ('+','.join('?' for _ in identifiers)+')',identifiers):
                body=json.loads(raw['raw_json']);key=(raw['snapshot_id'],body.get('ISU_CD'))
                raw_capitals[key]=None if key in raw_capitals else body
    for row in rows:
        r = dict(row); master = evidence['markets'].get(r['market'], {})
        try:
            good = (exact_code(r['stock_code']) and r['market'] in MARKETS
                    and STANDARD_CODE.fullmatch(r['standard_code'] or '')
                    and r['master_basis_date']==evidence['basis_date']
                    and r['master_body_hash']==master['body_sha256']
                    and r['validation_status']=='schema_and_dated_identity_checked'
                    and r['source_kind']=='krx_live' and instant(r['collected_at'])<=at
                    and isinstance(r['name'],str) and bool(r['name'].strip()))
        except (KeyError, TypeError, ValueError): good = False
        if not good:
            skipped += 1
            continue
        raw=raw_capitals.get((r.get('snapshot_id'),r.get('official_code_raw')))
        try:
            capital_match=bool(raw) and all(decimal_value(raw.get(field))==decimal_value(r.get(column)) and decimal_value(r.get(column)) is not None
                for field,column in [('MKTCAP','market_cap_raw'),('TDD_CLSPRC','close'),('LIST_SHRS','listed_shares')])
        except (ValueError,TypeError):capital_match=False
        records.append(dict(stock_code=r['stock_code'], market=r['market'],
            official_name=r['name'], abbreviated_name=r['name'], product_type=r['product_type'],
            basis_date=r['day'], standard_code=r['standard_code'],
            available_at=max(instant(r['collected_at']),instant(master['received_at'])).isoformat(),
            master_body_hash=r['master_body_hash'], collected_at=r['collected_at'],
            daily_capital=dict(basis_date=r['day'],snapshot_id=r.get('snapshot_id'),
                collected_at=r['collected_at'],raw_market_cap=r.get('market_cap_raw'),
                raw_close=r.get('close'),raw_listed_shares=r.get('listed_shares'),
                raw_fields_match=capital_match,
                source_fields={k:raw.get(k) for k in ('ISU_CD','MKTCAP','TDD_CLSPRC','LIST_SHRS')} if raw else None,
                stored_market_cap_won=r.get('market_cap_won')),
            name_basis='checked_KRX_daily_name_with_dated_official_master_join'))
    if len({r['stock_code'] for r in records}) != len(records):
        raise ValueError('ambiguous_official_cross_market_code')
    return records, dict(status='available_before_cutoff', **evidence, rows=len(records),
                         unchecked_rows_skipped=skipped, rejected_basis_dates=rejected,
                         raw_master_receipts_reloaded=False, as_of=as_of)


def turnover_evidence(path, snapshot):
    if not snapshot:
        return dict(status='missing', amount_score_allowed=False)
    with read_db(path) as db:
        run = dict(db.execute('SELECT id,pagination_complete,row_count,issues_json FROM turnover_runs WHERE id=?', (snapshot['id'],)).fetchone())
        pages = [dict(r) for r in db.execute('SELECT page_no,row_count,cont_yn FROM turnover_pages WHERE run_id=? ORDER BY page_no', (snapshot['id'],))]
        stored = [json.loads(r[0]) for r in db.execute('SELECT normalized_json FROM turnover_rows WHERE run_id=?', (snapshot['id'],))]
    return dict(status='partial' if not run['pagination_complete'] else 'complete',
                field='trde_prica', declared_units=sorted({str(r.get('turnover_source_unit')) for r in stored}),
                spec_verified=bool(stored) and all(r.get('unit_spec_verified') is True for r in stored),
                runtime_verified=bool(stored) and all(r.get('unit_runtime_verified') is True for r in stored),
                amount_score_allowed=False, pages=pages, rows=run['row_count'],
                pagination_complete=bool(run['pagination_complete']), issues=json.loads(run['issues_json']),
                request_started_at=snapshot['request_started_at'], response_received_at=snapshot['response_received_at'],
                market_asof=snapshot.get('market_asof'),
                unobserved_stock_policy='unknown_not_insufficient_turnover')
