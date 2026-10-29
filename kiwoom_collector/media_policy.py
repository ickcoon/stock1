"""Retired media execution policy. No environment/CLI override is supported.

Legacy implementations remain available for offline regression tests, whose
fake transports explicitly patch this guard. Existing reservations stay intact
and are logically quarantined: they cannot reach generation or voice delivery.
"""


def require_legacy_media():
    raise ValueError('legacy_voice_disabled')
