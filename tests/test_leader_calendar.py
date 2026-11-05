"""Fixture provenance and knowledge clocks, separate from actual external notes."""
from copy import deepcopy
import unittest
from kiwoom_collector import leader_calendar as cal


def loaded():
    row=dict(trade_date='2026-11-03',raw_theme='반도체 관련주',raw_stock_name='Fixture',
             source_kind='external_trader_close',sources=['fixture.png'])
    return dict(records=[row],theme_records=[dict(trade_date='2026-11-03',raw_theme='반도체 관련주',source='fixture.png')],
                metadata=dict(missing_capture_dates=[]))


class CalendarTests(unittest.TestCase):
    def test_close_records_cannot_be_known_at_0950(self):
        data=loaded();before=deepcopy(data)
        result=cal.query_day('2026-11-03',data,as_of='2026-11-03T09:50:00+09:00',materialized_at='2026-11-04T12:00:00+09:00')
        self.assertEqual(result['external_trader_close']['records'],[])
        self.assertEqual(result['external_trader_close']['theme_records'],[])
        self.assertEqual(data,before)

    def test_captures_are_not_arvis_selections_and_repeats_exact(self):
        data=loaded();r=deepcopy(data['records'][0]);r.update(trade_date='2026-11-02')
        data['records'].append(r)
        r=deepcopy(r);r['raw_theme']='반도체';data['records'].append(r)
        capture=dict(trade_date='2026-11-03',source_kind='stored_mock_0198_capture',observed_at='2026-11-03T09:50:00+09:00')
        result=cal.query_day('2026-11-03',data,as_of='2026-11-04T12:00:00+09:00',materialized_at='2026-11-04T12:00:00+09:00',captures=[capture])
        self.assertEqual(result['actual_selection_count'],0)
        self.assertEqual(result['captured_observation_count'],1)
        repeated={r['raw_theme']:r['appearance_days'] for r in result['external_theme_repetitions']}
        self.assertEqual(repeated,{'반도체':1,'반도체 관련주':2})

    def test_explicit_arvis_observation_and_episode_cutoff(self):
        own=dict(trade_date='2026-11-03',slot='09:50',source_kind='arvis_selection',
                 observed_at='2026-11-03T09:50:00+09:00',available_at='2026-11-03T09:50:02+09:00',selection_basis='FIXTURE_ONLY')
        episode=dict(d0='2026-11-03',as_of='2026-11-05T12:00:00+09:00',days=[])
        result=cal.query_day('2026-11-03',loaded(),as_of='2026-11-03T09:50:01+09:00',materialized_at='2026-11-04T12:00:00+09:00',arvis_records=[own],episodes=[episode])
        self.assertEqual(result['actual_selection_count'],0);self.assertEqual(result['tracking'],[])
        result=cal.query_day('2026-11-03',loaded(),as_of='2026-11-03T09:50:03+09:00',materialized_at='2026-11-04T12:00:00+09:00',arvis_records=[own])
        self.assertEqual(result['actual_selection_count'],1)
        self.assertEqual(result['external_trader_close']['records'],[])


if __name__=='__main__':unittest.main()
