"""Read-only archived daily responses -> C14-B; no HTTP, credentials or DB writes."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .. import daily_chart as daily
from ..am_candidate_adapter import normalize_code
from ..daily_tracking_adapter import ClosingEvidence
from ..leader_tracking import timestamp
from .context import analyze_context
from .daily_adapter import from_daily_pages


def read_json(path):
    path = Path(path).resolve(strict=True)
    before = path.read_bytes()
    document = json.loads(before.decode('utf-8-sig'))
    if path.read_bytes() != before:raise ValueError('saved_source_changed')
    return document, dict(path=str(path), sha256=hashlib.sha256(before).hexdigest(), hash_unchanged=True)


def diagnostic_pages(document):
    """Reparse the saved projection, never trust persisted derived rows or flags."""
    if not isinstance(document,dict) or not isinstance(document.get('results'),list):
        raise ValueError('saved_diagnostic_object_required')
    environment = document.get('environment')
    if environment not in ('mock','real'):raise ValueError('saved_environment_required')
    if document.get('version') != 'step7-b2-a-v1':raise ValueError('saved_version_required')
    if document.get('mode') == 'synthetic_fixture' and document.get('data_origin') == 'synthetic_fixture':
        origin = 'synthetic_fixture'
    elif (document.get('mode') == 'live_http_diagnostic' and document.get('data_origin') ==
          {'mock':'mock_server_unverified_source','real':'real_server_unverified_daily_bars'}[environment]):
        origin = 'api_response'
    else:raise ValueError('explicit_saved_origin_required')
    pages = []
    for item in document['results']:
        if not isinstance(item,dict):raise ValueError('saved_result_object_required')
        if item.get('status') not in ('parsed','parsed_with_issues','empty'):continue
        request = item['request'];body = request['body']
        scope = daily.DailyRequest(request['api_id'], body['stk_cd'], environment,
                                   body['base_dt'], body['upd_stkpc_tp'])
        headers = {k.lower():v for k,v in item['response_headers'].items()}
        page = daily.parse_response(item['response_projection'], request=scope,
            response_environment=item['environment'], request_started_at=item['request_started_at'],
            response_received_at=item['response_received_at'], http_status=item['http_status'],
            source='step7-b2-a documented-field diagnostic projection',
            cont_yn=headers.get('cont-yn'), next_key=headers.get('next-key'),
            response_api_id=headers.get('api-id'))
        pages.append(page)
    return tuple(pages), origin


def c12_context(document, request, *, data_origin, as_of):
    """Join C12's saved candidate_filter.decisions without changing eligibility."""
    block = document['candidate_filter']
    expected_kind = 'fixture' if data_origin == 'synthetic_fixture' else 'readonly_adapter'
    if (block.get('version') != 'c12-am-candidate-join-v1'
            or document.get('environment') != request.environment
            or document.get('market') != request.market or request.market != 'KRX'
            or document.get('source_kind') != expected_kind
            or timestamp(block['at']) > timestamp(as_of)):
        raise ValueError('saved_c12_scope_or_cutoff_mismatch')
    index = {}
    for original, decision in block['decisions'].items():
        code = normalize_code(original)
        if code is None or code in index:raise ValueError('ambiguous_saved_c12_code')
        index[code] = deepcopy(decision)
    return dict(stock_code=request.stock_code, environment=request.environment, market=request.market,
        data_origin=data_origin, available_at=block['at'], source='saved C12 candidate_filter',
        decision=index.get(request.stock_code, dict(state='unmatched', reasons=['master_code_unmatched'])),
        c12_source_kind=document['source_kind'], c12_data_origin=document.get('data_origin','unverified'),
        master_sha256=block.get('master_sha256'))


def analyze_saved(paths, *, as_of, reviews=(), candidate_document=None):
    """Reviews are external assertions bound to exact request/origin/page fingerprints.

    With no review: no calendar, finality, basis or selection evidence is invented.
    Raw parsed pages remain available for future review, but metrics are unavailable.
    """
    cutoff = timestamp(as_of)
    grouped, origins, sources, provenance = defaultdict(list), {}, defaultdict(list), []
    for path in paths:
        archive, proof = read_json(path)
        pages, origin = diagnostic_pages(archive)
        provenance.append(proof)
        for page in pages:
            if page.request in origins and origins[page.request] != origin:
                raise ValueError('mixed_saved_data_origins')
            if page.fingerprint in {p.fingerprint for p in grouped[page.request]}:
                raise ValueError('duplicate_saved_page')
            grouped[page.request].append(page)
            origins[page.request] = origin
            sources[page.request].append(proof)
    review_index = {}
    for review in reviews:
        scope = review['scope']
        key = daily.DailyRequest(scope['api_id'], scope['stock_code'], scope['environment'],
                                 scope['base_dt'], scope['adjustment'])
        if key in review_index or key not in grouped:raise ValueError('ambiguous_or_unmatched_saved_review')
        if scope.get('data_origin') != origins[key]:raise ValueError('saved_review_origin_mismatch')
        if sorted(review['page_fingerprints']) != sorted(p.fingerprint for p in grouped[key]):
            raise ValueError('saved_review_page_binding_mismatch')
        review_index[key] = review
    reports = []
    for request, pages in grouped.items():
        review = review_index.get(request, {})
        evidence = [ClosingEvidence(**item) for item in review.get('evidence', ())]
        valid_rows = {(p.fingerprint,r.fingerprint) for p in pages for r in p.rows}
        if any((e.page_fingerprint,e.row_fingerprint) not in valid_rows for e in evidence):
            raise ValueError('saved_review_row_binding_mismatch')
        candidate = c12_context(candidate_document, request, data_origin=origins[request], as_of=cutoff) if candidate_document else None
        selection = review.get('selection', {})
        if not selection and candidate:
            selection = dict(stock_code=request.stock_code, source=candidate['source'],
                available_at=candidate['available_at'], reference=candidate.get('master_sha256'),
                candidate_state=candidate['decision']['state'])
        document = from_daily_pages(pages, stock_name=review.get('stock_name',request.stock_code),
            as_of=cutoff.isoformat(), price=review.get('price',{}), selection=selection,
            calendar=review.get('calendar',dict(verified=False,completed_sessions=[])),
            price_basis=review.get('price_basis',dict(mode={'0':'original','1':'adjusted'}[request.adjustment],verified=False)),
            evidence=evidence, context_id=review.get('context_id','unreviewed-saved-pages'),
            expected_session_basis='regular_session' if request.api_id == 'ka11081' else 'chart_session',
            data_origin=origins[request])
        result = analyze_context(document, candidate_context=candidate)
        # Provenance is outside the hashed analysis: no fabricated finality or calendar.
        reports.append(dict(request=dict(api_id=request.api_id,stock_code=request.stock_code,
            environment=request.environment,base_dt=request.base_dt,adjustment=request.adjustment),
            sources=sources[request], parsed_rows=sum(len(p.rows) for p in pages),
            page_fingerprints=[p.fingerprint for p in pages], review_supplied=bool(review), report=result))
    if any(hashlib.sha256(Path(p['path']).read_bytes()).hexdigest()!=p['sha256'] for p in provenance):
        raise ValueError('saved_source_changed_during_analysis')
    any_metric = lambda item: any(v['value'] is not None for v in item['report']['moving_averages'].values())
    return dict(schema_version='arvis-saved-chart-batch-v1',as_of=cutoff.isoformat(),sources=provenance,
        reports=reports, summary=dict(report_count=len(reports),
            instrument_count=len({(r['request']['environment'],r['request']['stock_code']) for r in reports}),
            parsed_rows=sum(r['parsed_rows'] for r in reports),
            with_any_ma_count=sum(any_metric(r) for r in reports),
            high52_available_count=sum(r['report']['high52']['status']=='available' for r in reports),
            supply_available_count=sum(r['report']['supply_proxy']['status']=='available' for r in reports),
            fully_analyzable_count=sum(r['report']['coverage']['status']=='complete' for r in reports),
            api_response_reports=sum(r['report']['data_origin']=='api_response' for r in reports),
            synthetic_reports=sum(r['report']['data_origin']=='synthetic_fixture' for r in reports)),
        oauth_calls=0,api_calls=0,database_writes=0)
