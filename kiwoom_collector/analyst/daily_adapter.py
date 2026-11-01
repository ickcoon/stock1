"""Step 7-B saved pages -> analyst input. No raw-price tracking rule changes."""
from copy import deepcopy

from ..daily_chart import parse_response


def from_daily_pages(pages, *, stock_name, as_of, price, selection, calendar, price_basis,
                     evidence, context_id, expected_session_basis, data_origin):
    """Reuse bound ClosingEvidence; adjustment comparability is a separate review."""
    pages = tuple(pages)
    if not pages or not context_id:raise ValueError('pages_and_context_required')
    witnesses = {}
    for proof in evidence:
        key = (proof.page_fingerprint, proof.row_fingerprint)
        if key in witnesses:raise ValueError('duplicate_chart_evidence')
        witnesses[key] = proof
    request = pages[0].request
    bars = []
    for page in pages:
        parsed = parse_response(page.raw, request=page.request, response_environment=page.request.environment,
            request_started_at=page.request_started_at, response_received_at=page.response_received_at,
            source=page.source, cont_yn=page.cont_yn, next_key=page.next_key, response_api_id=page.response_api_id)
        if parsed != page:raise ValueError('daily_page_raw_model_mismatch')
        if page.request != request:raise ValueError('mixed_chart_request_scope')
        for row in page.rows:
            proof = witnesses.get((page.fingerprint, row.fingerprint))
            price_issues = [i for i in row.issues if not i.startswith(('volume_', 'negative:trde_qty',
                'missing:trde_qty', 'invalid_numeric:trde_qty', 'missing:trde_prica', 'invalid_numeric:trde_prica', 'negative:trde_prica'))]
            price_issues += [i for i in page.issues if i != 'daily_order_unverified']
            if {'0':'original', '1':'adjusted'}[request.adjustment] != price_basis.get('mode'):
                price_issues.append('request_adjustment_basis_mismatch')
            expected_api_basis = 'regular_session' if request.api_id == 'ka11081' else 'chart_session'
            valid_proof = bool(proof and proof.episode_id == context_id and proof.source
                and proof.session_basis == expected_session_basis == expected_api_basis)
            if not valid_proof:price_issues.append('bound_finality_or_session_evidence_missing')
            if proof and (proof.market_at > page.request_started_at or proof.verified_at < page.response_received_at):
                price_issues.append('source_receipt_or_finality_chronology_invalid')
            available = max(page.response_received_at, proof.verified_at) if proof else page.response_received_at
            bars.append(dict(trade_date=row.trade_date.isoformat() if row.trade_date else None,
                **{k:str(getattr(row,k)) if getattr(row,k) is not None else None for k in ('open','high','low','close','volume')},
                status=proof.status if valid_proof else 'unverified',
                market_at=proof.market_at.isoformat() if proof else None,
                finalized_at=proof.verified_at.isoformat() if proof else None, available_at=available.isoformat(),
                source=proof.source if valid_proof else page.source,
                price_unit=proof.price_unit if valid_proof else None,
                volume_unit=proof.volume_unit if valid_proof else None,
                session_basis=proof.session_basis if valid_proof else None,
                basis_id=price_basis.get('id'), price_issues=price_issues,
                volume_issues=[i for i in row.issues if 'trde_qty' in i or i.startswith('volume_')],
                source_reference=dict(api_id=request.api_id, page_fingerprint=page.fingerprint,
                    row_fingerprint=row.fingerprint, source_row_index=row.index,
                    request_started_at=page.request_started_at.isoformat(),
                    response_received_at=page.response_received_at.isoformat(),
                    adjustment=request.adjustment, pagination=page.pagination,
                    original_ordering=page.ordering),
                api_raw_values={k:v for k,v in row.raw.items() if k in (
                    'dt','open_pric','high_pric','low_pric','cur_prc','trde_qty','upd_rt','upd_stkpc_tp')}))
    return dict(schema_version='analyst-input-v1', stock_code=request.stock_code, stock_name=stock_name,
        as_of=as_of, environment=request.environment, market=request.market, data_origin=data_origin,
        session_basis=expected_session_basis, bars=bars, price=deepcopy(price), selection=deepcopy(selection),
        calendar=deepcopy(calendar), price_basis=deepcopy(price_basis))
