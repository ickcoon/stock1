"""Offline saved-projection review; never reads credentials, DBs or live APIs."""
from dataclasses import dataclass
import json
from pathlib import Path

from . import daily_chart as daily, daily_tracking_adapter as adapter, leader_tracking as tracking

VERSION = 'step7c2-v1'
UNITS = {'price': 'KRW', 'volume': 'share', 'turnover': 'million_KRW'}
ROW_FIELDS = {'stk_cd', 'dt', 'open_pric', 'high_pric', 'low_pric', 'cur_prc',
              'trde_qty', 'trde_prica', 'pred_pre', 'pred_pre_sig', 'trde_tern_rt',
              'upd_stkpc_tp', 'upd_rt'}


@dataclass(frozen=True)
class SavedPage:
    page: daily.DailyPage
    projection_id: str
    data_origin: str | None
    unit_issues: tuple[str, ...]


def load_report(path):
    """Read an explicitly supplied diagnostic JSON only; no directory discovery."""
    document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(document, dict):
        raise ValueError('Diagnostic report object required')
    return document


def parse_report(document):
    """Reparse safe response_projection, ignoring cached derived rows/approval flags."""
    if not isinstance(document, dict):
        raise ValueError('Diagnostic report object required')
    environment = document.get('environment')
    results = document.get('results')
    if environment not in ('mock', 'real') or not isinstance(results, list) or not results:
        raise ValueError('Saved report environment/results required')
    pages = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError('Saved response object required')
        if result.get('environment', environment) != environment:
            raise ValueError('Mixed report environment')
        if not str(result.get('projection_policy', '')).startswith('documented_fields_only'):
            raise ValueError('Documented-field diagnostic projection required')
        body = result.get('response_projection')
        if not isinstance(body, dict) or set(body) - {'stk_cd', 'return_code', 'return_msg', *daily.LIST_KEYS.values()}:
            raise ValueError('Unsafe or missing response projection')
        for key in daily.LIST_KEYS.values():
            if key in body and isinstance(body[key], list):
                for row in body[key]:
                    if not isinstance(row, dict) or set(row) - ROW_FIELDS:
                        raise ValueError('Only documented projected row fields accepted')
                    if any(isinstance(v, (dict, list)) for v in row.values()):
                        raise ValueError('Nested projected row values rejected')
        request_data = result.get('request') or {}
        raw_request = request_data.get('body') or {}
        request = daily.DailyRequest(request_data.get('api_id'), raw_request.get('stk_cd'), environment,
                                     raw_request.get('base_dt'), raw_request.get('upd_stkpc_tp'))
        headers = {k.lower(): v for k, v in (result.get('response_headers') or {}).items()
                   if k.lower() in ('api-id', 'cont-yn', 'next-key')}
        start = tracking.timestamp(result.get('request_started_at'))
        received = tracking.timestamp(result.get('response_received_at'))
        # This identifies the redacted projection, NOT the unavailable full response.
        identity = daily.fingerprint([environment, request.api_id, request.body, body,
                                      start.isoformat(), received.isoformat(), headers])
        page = daily.parse_response(body, request=request, response_environment=environment,
            request_started_at=start, response_received_at=received,
            source='saved_diagnostic_projection:' + identity, http_status=result.get('http_status'),
            cont_yn=headers.get('cont-yn'), next_key=headers.get('next-key'),
            response_api_id=headers.get('api-id'))
        reported = result.get('units') or {}
        issues = tuple('report_unit_' + ('missing:' if reported.get(k) is None else 'mismatch:') + k
                       for k, value in UNITS.items() if reported.get(k) != value)
        pages.append(SavedPage(page, identity, document.get('data_origin'), issues))
    return tuple(pages)


def review_reports(reports, *, as_of, snapshot=None, episode_id=None, calendar=None,
                   basis=None, evidence=()):
    """Join one episode to saved query pages; eligibility always remains false.

    basis is caller-reviewed D0 comparison context, not closing proof. External
    ClosingEvidence alone may create confirmed observations through the B1 gate.
    """
    cutoff = tracking.timestamp(as_of)
    if snapshot is not None:
        if episode_id != snapshot.episode_id:
            raise ValueError('Explicit matching D0 episode_id required')
        if cutoff < snapshot.captured_at:
            raise ValueError('Snapshot not yet available')
    elif episode_id is not None:
        raise ValueError('Episode id cannot substitute for a D0 Snapshot')
    calendar = calendar or tracking.FixtureCalendar((), (), 'calendar_not_provided')
    evidence = tuple(evidence)
    pages = []
    seen = set()
    duplicates = 0
    for document in reports:
        for saved in parse_report(document):
            if saved.projection_id in seen:
                duplicates += 1
                continue
            seen.add(saved.projection_id)
            pages.append(saved)
    identities = {(s.page.request.stock_code, s.page.request.environment, s.page.request.market) for s in pages}
    if len(identities) > 1:
        raise ValueError('Mixed stock/environment/market reports forbidden')
    if snapshot and identities and identities != {(snapshot.stock_code, snapshot.environment, snapshot.market)}:
        raise ValueError('Snapshot stock/environment/market mismatch')
    if len({s.page.request.adjustment for s in pages}) > 1:
        raise ValueError('Original and adjusted price reports cannot mix')
    if len({s.page.request.api_id for s in pages}) > 1:
        raise ValueError('Different chart API/session reports cannot mix')
    targets = {i: calendar.offset(snapshot.d0, i) for i in (0, 1, 2)} if snapshot else {}
    calendar_known = bool(snapshot and all(d is not None for d in targets.values()))
    holds, observations, candidates, inputs = [], [], [], []
    conflicts = {}
    for saved in pages:
        page = saved.page
        if page.response_received_at > cutoff:
            # Do not expose even candidate prices from future or late-arriving pages.
            holds.append({'projection_id': saved.projection_id, 'reasons': ('response_not_yet_available',)})
            continue
        api_basis = 'regular_session' if page.request.api_id == 'ka11081' else 'chart_session'
        context_reasons = list(saved.unit_issues)
        if page.request.adjustment != '0':
            context_reasons.append('adjusted_history_not_d0_observed_prices')
        if basis is None:
            context_reasons.append('d0_basis_unverified')
        else:
            for key, value in UNITS.items():
                if basis.get(key) != value:
                    context_reasons.append('basis_unit_mismatch:' + key)
            if basis.get('adjustment') != '0' or basis.get('session_basis') != api_basis:
                context_reasons.append('price_or_session_basis_mismatch')
            if basis.get('d0_compatible') is not True:
                context_reasons.append('d0_basis_unverified')
        if snapshot is None:
            context_reasons.append('d0_snapshot_missing')
        inputs.append({'projection_id': saved.projection_id, 'page_fingerprint': page.fingerprint,
            'stock_code': page.request.stock_code, 'environment': page.request.environment,
            'market': page.request.market, 'api_id': page.request.api_id, 'base_dt': page.request.base_dt,
            'adjustment': page.request.adjustment, 'response_received_at': page.response_received_at.isoformat(),
            'source_kind': 'redacted_api_projection', 'data_origin': saved.data_origin,
            'pagination': page.pagination, 'units_status': page.units['status']})
        for row in page.rows:
            key = (row.trade_date, page.response_received_at)
            signature = (row.raw_json, page.request.api_id, page.request.adjustment)
            if key in conflicts and conflicts[key] != signature:
                raise ValueError('Conflicting daily observations at identical receipt time')
            conflicts[key] = signature
        conversion = None
        if not context_reasons:
            # Different base_dt queries are converted separately, never concatenated pages.
            conversion = adapter.to_tracking((page,), snapshot, calendar=calendar, as_of=cutoff,
                expected_session_basis=api_basis,
                evidence=tuple(p for p in evidence if p.page_fingerprint == page.fingerprint))
            observations.extend(conversion.observations)
        row_holds = {row_id: reasons for _, row_id, reasons in conversion.holds} if conversion else {}
        approved = {json.loads(o.raw_json)['row_fingerprint'] for o in conversion.observations} if conversion else set()
        for row in page.rows:
            reasons = list(context_reasons) + list(page.issues) + list(row.issues)
            if row.trade_date is None or calendar.offset(row.trade_date, 0) != row.trade_date:
                reasons.append('trading_day_unverified')
            reasons.extend(row_holds.get(row.fingerprint, ()))
            if row.fingerprint not in approved:
                reasons.append('close_not_verified')
            if reasons:
                holds.append({'projection_id': saved.projection_id, 'row_fingerprint': row.fingerprint,
                              'trade_date': row.trade_date.isoformat() if row.trade_date else None,
                              'reasons': tuple(dict.fromkeys(reasons))})
            if row.fingerprint in approved:
                continue
            if row.trade_date is None or 'future_or_after_base_date' in row.issues:
                continue  # Quarantined future/invalid dates never expose preview prices.
            offset = next((i for i, day in targets.items() if day is not None and day == row.trade_date), None)
            if calendar_known and offset is None:
                continue  # History is retained by the page; preview only D0..D2.
            baseline = snapshot.d0_bar if snapshot else None
            usable = (not context_reasons and calendar_known and not page.issues and not row.issues
                      and baseline is not None and baseline.close is not None)
            candidates.append({'projection_id': saved.projection_id, 'trade_date': row.trade_date.isoformat() if row.trade_date else None,
                'offset': offset, 'response_received_at': page.response_received_at.isoformat(),
                'market_at': None, 'verified_at': None, 'value_status': 'provisional',
                'close_candidate': row.close, 'volume_candidate': row.volume, 'turnover_won_candidate': row.turnover_won,
                'provisional': {'change_d0_percent': tracking.percent(row.close, baseline.close) if usable else None},
                'vwap': None, 'eligible_for_tracking': False,
                'reasons': tuple(dict.fromkeys(reasons + ([] if baseline and baseline.close else ['d0_reference_price_missing'])))})
    # Identical imports/events never inflate the confirmed event list; later revisions remain separate.
    observations = tuple(dict.fromkeys(observations))
    engine = tracking.analyze(snapshot, observations, calendar=calendar, as_of=cutoff) if snapshot else None
    return {'version': VERSION, 'episode_id': snapshot.episode_id if snapshot else None,
            'as_of': cutoff.isoformat(), 'status': 'd0_snapshot_missing' if snapshot is None else
                'calendar_unknown' if not calendar_known else 'reviewed',
            'calendar_source': getattr(calendar, 'source', None),
            'trade_dates': {i: d.isoformat() if d else None for i, d in targets.items()},
            'eligible_for_tracking': False, 'candidate_confirmed': False,
            'observations': observations, 'provisional': tuple(candidates), 'holds': tuple(holds),
            'tracking': engine, 'inputs': tuple(inputs), 'duplicate_imports_ignored': duplicates}
