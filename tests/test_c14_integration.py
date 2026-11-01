"""Synthetic C14 integration smoke; isolated temporary files, no network."""
import json
import unittest
from unittest.mock import patch

import test_weekly_probe as probe
from kiwoom_collector.analyst.context import analyze_context
from kiwoom_collector.daily_chart import fingerprint


class C14IntegrationTests(unittest.TestCase):
    def test_daily_weekly_and_hybrid_json_contracts(self):
        helper=probe.WeeklyProbeTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        with patch('kiwoom_collector.weekly_probe_cli.credentials',
                   side_effect=AssertionError('no actual credentials')):
            daily=analyze_context(helper.doc)
            self.assertEqual(daily['schema_version'],'arvis-chart-context-v1')
            self.assertEqual(daily['high52']['status'],'available')
            _,standalone=helper.standalone(helper.root/'weekly-only')
            code,summary=helper.execute(['--replay-json',str(helper.fixture()),
                '--weekly-review-json',str(helper.review())],helper.root/'hybrid')
            self.assertEqual(code,0)
            hybrid=json.loads((helper.root/'hybrid'/'hybrid.json').read_text(encoding='utf-8'))
            self.assertEqual(hybrid['daily'],daily)
            self.assertEqual(standalone['primary_high52']['prior_high'],hybrid['primary_high52']['prior_high'])
            self.assertEqual(standalone['daily']['status'],'unavailable')
            self.assertEqual(summary['collection']['api_calls'],0)
            self.assertFalse(summary['live_requests_attempted'])
            for result in (hybrid,standalone):
                self.assertEqual(result['schema_version'],'arvis-hybrid-chart-v1')
                self.assertFalse(result['order_signal'])
                self.assertEqual(result['data_origin'],'synthetic_fixture')
                digest=result.pop('hybrid_reference')
                self.assertEqual(digest,fingerprint(result))
                json.dumps(result,allow_nan=False)
