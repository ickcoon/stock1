"""Explicit AM reference windows; no timestamp rewriting or cross-day grace."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def instant(value):
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            raise ValueError('aware_timestamp_required') from None
    if result.tzinfo is None:
        raise ValueError('aware_timestamp_required')
    return result.astimezone(KST)


@dataclass(frozen=True)
class ObservationPolicy:
    version: str = 'am-window-v1'
    receipt_grace_seconds: int = 45
    observation_before_seconds: int = 90
    observation_after_seconds: int = 30
    capital_ttl_seconds: int = 3600

    def __post_init__(self):
        if self.version != 'am-window-v1':
            raise ValueError('unsupported_observation_policy')
        for key in ('receipt_grace_seconds', 'observation_before_seconds', 'observation_after_seconds'):
            if type(getattr(self, key)) is not int or not 0 <= getattr(self, key) <= 300:
                raise ValueError('invalid_observation_window')
        if type(self.capital_ttl_seconds) is not int or not 1 <= self.capital_ttl_seconds <= 86400:
            raise ValueError('invalid_capital_ttl')

    def document(self):
        return asdict(self)

    def deadline(self, reference):
        return instant(reference)+timedelta(seconds=self.receipt_grace_seconds)

    def available(self, observed, received, reference):
        try:
            o, r, a = map(instant, (observed, received, reference))
            return o.date() == r.date() == a.date() and o <= r <= self.deadline(a)
        except ValueError:
            return False

    def near(self, observed, received, reference):
        if not self.available(observed, received, reference):
            return False
        delta = (instant(observed)-instant(reference)).total_seconds()
        return -self.observation_before_seconds <= delta <= self.observation_after_seconds

    def cached_capital(self, observed, received, reference, ttl):
        if type(ttl) is not int or not 1 <= ttl <= self.capital_ttl_seconds:
            return False
        return (self.available(observed, received, reference)
                and 0 <= (self.deadline(reference)-instant(received)).total_seconds() <= ttl
                and 0 <= (self.deadline(reference)-instant(observed)).total_seconds() <= ttl)


def from_document(document):
    value = document.get('observation_policy')
    if value is None:
        return None  # Old reports retain their strict, reproducible contract.
    if not isinstance(value, dict) or set(value) != set(ObservationPolicy().document()):
        raise ValueError('invalid_observation_policy')
    return ObservationPolicy(**value)


def available(policy, observed, received, reference):
    if policy:
        return policy.available(observed, received, reference)
    try:
        o, r, a = map(instant, (observed, received, reference))
        return o.date() == r.date() == a.date() and o <= r <= a
    except ValueError:
        return False


def near(policy, observed, received, reference):
    if policy:
        return policy.near(observed, received, reference)
    return available(None, observed, received, reference) and instant(observed).strftime('%H:%M') == instant(reference).strftime('%H:%M')
