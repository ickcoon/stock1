"""Evidence-gated daily parser -> Step 7-A observations; no snapshot writes."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from . import leader_tracking as tracking
from .daily_chart import VERSION, parse_response


@dataclass(frozen=True)
class ClosingEvidence:
    """Externally reviewed evidence, NOT evidence manufactured from dt or wall time."""
    page_fingerprint: str
    row_fingerprint: str
    episode_id: str
    market_at: datetime
    verified_at: datetime
    source: str
    session_basis: str
    price_unit: str | None = None
    volume_unit: str | None = None
    turnover_unit: str | None = None
    original_price_basis_verified: bool = False
    d0_basis_compatible: bool = False
    status: str = 'unverified'

    def __post_init__(self):
        object.__setattr__(self, 'market_at', tracking.timestamp(self.market_at))
        object.__setattr__(self, 'verified_at', tracking.timestamp(self.verified_at))
        if self.status not in ('unverified', 'incomplete', 'final', 'halted'):
            raise ValueError('Explicit closing evidence status required')


@dataclass(frozen=True)
class Conversion:
    observations: tuple[tracking.Observation, ...]
    # (page fingerprint, row fingerprint, hold reasons); originals stay in DailyPage.
    holds: tuple[tuple[str, str, tuple[str, ...]], ...]


def to_tracking(pages, snapshot, *, calendar, as_of, expected_session_basis, evidence=()):
    """Only explicit, bound, contemporaneously known final bars enter tracking.

    Price scopes must be reviewed against D0. Adjusted historical bars never enter
    this raw-price tracker. Unreceived dates are not synthesized from empty pages.
    """
    cutoff = tracking.timestamp(as_of)
    if cutoff < snapshot.captured_at:
        raise ValueError('Snapshot not yet available')
    pages = tuple(pages)
    witnesses = {}
    for proof in evidence:
        key = (proof.page_fingerprint, proof.row_fingerprint)
        if key in witnesses:
            raise ValueError('Duplicate evidence')
        witnesses[key] = proof
    all_dates = []
    for page in pages:
        # Reuse the parser: copied derived values or cleared issues must still
        # agree with the saved response before its fingerprints can be trusted.
        parsed = parse_response(
            page.raw, request=page.request, response_environment=page.request.environment,
            request_started_at=page.request_started_at,
            response_received_at=page.response_received_at, source=page.source,
            cont_yn=page.cont_yn, next_key=page.next_key,
            response_api_id=page.response_api_id)
        if parsed != page:
            raise ValueError('Daily page model does not match its raw response')
        if (page.request.stock_code, page.request.environment, page.request.market) != (
                snapshot.stock_code, snapshot.environment, snapshot.market):
            raise ValueError('Snapshot stock/environment/market mismatch')
        if pages and page.request != pages[0].request:
            raise ValueError('Mixed chart API/base date/adjustment context')
        all_dates.extend(r.trade_date for r in page.rows if r.trade_date)
    duplicate_dates = {d for d, count in Counter(all_dates).items() if count > 1}
    cross_order_bad = any(a < b for a, b in zip(all_dates, all_dates[1:]))
    observations, holds = [], []
    for page in pages:
        for row in page.rows:
            reasons = list(page.issues) + [i for i in row.issues if i != 'missing:trde_prica']
            if row.trade_date in duplicate_dates:
                reasons.append('duplicate_date_across_input')
            if cross_order_bad:
                reasons.append('cross_page_order_unverified')
            if page.response_received_at > cutoff:
                reasons.append('response_not_yet_available')
            if row.trade_date is None or calendar.offset(row.trade_date, 0) != row.trade_date:
                reasons.append('trading_day_unverified')
            if page.request.adjustment != '0':
                reasons.append('adjusted_history_not_d0_observed_prices')
            proof = witnesses.get((page.fingerprint, row.fingerprint))
            if proof is None:
                reasons.append('closing_evidence_missing')
            else:
                if proof.episode_id != snapshot.episode_id or not proof.source:
                    reasons.append('evidence_binding_invalid')
                if (proof.market_at.date() != row.trade_date or proof.market_at > page.request_started_at
                        or proof.verified_at < page.response_received_at or proof.verified_at > cutoff
                        or proof.market_at > proof.verified_at):
                    reasons.append('closing_chronology_unverified')
                if proof.status not in ('final', 'halted'):
                    reasons.append('daily_bar_not_final')
                if (proof.price_unit, proof.volume_unit, proof.turnover_unit) != ('KRW', 'share', 'million_KRW'):
                    reasons.append('units_unverified')
                api_basis = 'regular_session' if page.request.api_id == 'ka11081' else 'chart_session'
                if not expected_session_basis or proof.session_basis != expected_session_basis or proof.session_basis != api_basis:
                    reasons.append('session_basis_mismatch_or_unknown')
                if proof.original_price_basis_verified is not True or proof.d0_basis_compatible is not True:
                    reasons.append('d0_price_volume_basis_unverified')
                if proof.status == 'halted':
                    # Explicit halt evidence is independent of placeholder/absent OHLCV.
                    reasons = [r for r in reasons if not r.startswith(('missing:', 'zero_price:'))]
            if reasons:
                holds.append((page.fingerprint, row.fingerprint, tuple(dict.fromkeys(reasons))))
                continue
            halted = proof.status == 'halted'
            raw = dict(api_id=page.request.api_id, request=page.request.body,
                       request_started_at=page.request_started_at.isoformat(),
                       response_received_at=page.response_received_at.isoformat(),
                       page_fingerprint=page.fingerprint, row_fingerprint=row.fingerprint,
                       row=row.raw, units=page.units, adjustment=page.request.adjustment,
                       session_basis=proof.session_basis, evidence_source=proof.source,
                       evidence_verified_at=proof.verified_at.isoformat(),
                       pagination=page.pagination, cont_yn=page.cont_yn, next_key=page.next_key)
            observations.append(tracking.Observation(
                stock_code=snapshot.stock_code, environment=page.request.environment,
                market=page.request.market, trade_date=row.trade_date, market_at=proof.market_at,
                available_at=proof.verified_at, phase='post_market', source=page.source,
                status='halted' if halted else 'received',
                **{key: None if halted else getattr(row, key) for key in
                   ('open', 'high', 'low', 'close', 'volume', 'turnover_won')},
                raw_json=tracking.canonical(raw), version=VERSION))
    return Conversion(tuple(observations), tuple(holds))
