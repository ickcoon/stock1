"""Advancing request clock for offline HTTP fixtures (no real pacing waits)."""
from contextlib import contextmanager
from unittest.mock import patch


class RequestClock:
    def __init__(self):
        self.seconds = 100.0
        self.sleeps = []

    def now(self):
        return self.seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.seconds += seconds


@contextmanager
def request_clock():
    clock = RequestClock()
    with patch('kiwoom_collector.client.time.perf_counter', side_effect=clock.now), \
         patch('kiwoom_collector.client.time.sleep', side_effect=clock.sleep):
        yield clock
