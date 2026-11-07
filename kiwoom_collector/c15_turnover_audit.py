"""Offline saved-response reconciliation and documented-unit display, never scoring proof."""
from decimal import Decimal, localcontext
import json

from .am_momentum import number
from .c12_latest import read_db, digest


def documented_amount(raw, contract):
    value=number(raw)
    if (value is None or value<0 or contract.get('api_id')!='ka10032'
            or contract.get('amount_field')!='trde_prica' or contract.get('unit')!='million_KRW'
            or contract.get('multiplier_to_won')!='1000000'):
        return dict(raw_value=raw, documented_won=None, documented_eok=None, scoring_allowed=False)
    with localcontext() as context:
        context.prec=100
        won=value*Decimal(1000000)
        eok=won/Decimal(100000000)
    def text(value):
        rendered=format(value,'f')
        return rendered.rstrip('0').rstrip('.') if '.' in rendered else rendered
    return dict(raw_value=raw, documented_won=text(won), documented_eok=text(eok),
                verification='official_contract_conversion_not_empirical_verification',scoring_allowed=False)


def audit(path, snapshot, contract):
    if not snapshot: return dict(status='missing',amount_score_enabled=False,rows=[])
    with read_db(path) as db:
        pages={r['page_no']:json.loads(r['response_json']).get('trde_prica_upper',[])
               for r in db.execute('SELECT page_no,response_json FROM turnover_pages WHERE run_id=?',(snapshot['id'],))}
        saved=[dict(r) for r in db.execute('SELECT page_no,row_no,stock_code,raw_json,normalized_json FROM turnover_rows WHERE run_id=? ORDER BY page_no,row_no',(snapshot['id'],))]
    rows=[]
    snapshot_rows={r['stock_code']:json.loads(r['raw_json']) for r in snapshot['rows']}
    for row in saved:
        raw=json.loads(row['raw_json']); normalized=json.loads(row['normalized_json'])
        page=pages.get(row['page_no'],[]);ordinal=row['row_no']-1
        response=page[ordinal] if 0<=ordinal<len(page) else None
        same=(response==raw and raw==snapshot_rows.get(row['stock_code']) and raw.get('stk_cd')==row['stock_code'])
        converted=documented_amount(raw.get('trde_prica'),contract)
        rows.append(dict(stock_code=row['stock_code'],name=raw.get('stk_nm'),page_no=row['page_no'],row_no=row['row_no'],
            page_row_snapshot_equal=same, response_raw_value=(response or {}).get('trde_prica'),
            stored_source_unit=normalized.get('turnover_source_unit'),
            declared_unit_equal=normalized.get('turnover_source_unit')==contract.get('unit'),
            stored_runtime_verified=normalized.get('unit_runtime_verified'),
            stored_diagnostic_won=normalized.get('turnover_won_diagnostic'),
            diagnostic_conversion_equal=converted['documented_won'] is not None and normalized.get('turnover_won_diagnostic')==converted['documented_won'],
            **converted))
    consistent=(bool(rows) and len(rows)==len(snapshot['rows'])==sum(len(p) for p in pages.values())
                and all(r['page_row_snapshot_equal'] and r['diagnostic_conversion_equal'] and r['declared_unit_equal'] for r in rows))
    return dict(status='storage_consistent' if consistent else 'storage_mismatch',
                snapshot_sha256=snapshot['snapshot_sha256'],contract_hash=digest(contract),
                contract=contract,rows=rows,checked_rows=len(rows),storage_consistent=consistent,
                saved_response_is_sanitized_subset=True,wire_bytes_preserved=False,
                verification='documented_unit_and_saved_values_only',amount_score_enabled=False,
                activation_comparison=dict(before_enabled=False,after_enabled=False,
                    enabled_result=None,reason='no_independent_same_time_unit_or_cumulative_period_proof'))
