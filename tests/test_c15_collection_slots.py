"""C15 scheduling/association checks without market rows or network calls."""
from datetime import date
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from kiwoom_collector import schedule
from kiwoom_collector.operating_workflow import Workflow


class CollectionSlotsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = SimpleNamespace(path=self.root/'data/stock1-real.db')
        self.client = Mock(_last_request=None, request_interval=1.2)
        self.workflow = Workflow(self.root, {})
        self.workflow.review = Mock(return_value={'status': 'held'})
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('network forbidden')))
        self.enterContext(patch('kiwoom_collector.operating_workflow.LiveTransport',
                                return_value=Mock(_last_request=None)))
        self.enterContext(patch('kiwoom_collector.weekly_tls.configure_windows_trust'))

    def run_slot(self, slot):
        due = schedule.slot_at(date(2026, 11, 5), slot)
        self.workflow(self.client, self.store, 'real', due, 'ranking-reference', 0)
        return json.loads((self.root/'reports/workflow/2026-11-05'/
                           (slot.replace(':', '')+'.json')).read_text(encoding='utf8'))

    def test_three_slots_collect_and_share_observation_id(self):
        with patch('kiwoom_collector.operating_workflow.collect_turnover',
                   return_value={'status': 'partial', 'run_id': 'turnover-reference'}) as collect:
            for slot in ('09:40', '09:45', '09:50'):
                self.assertIn(slot, schedule.C12X_SLOTS)
                self.assertIn(slot, schedule.TURNOVER_SLOTS)
                self.assertEqual(schedule.C12X_SLOTS.count(slot),1)
                self.assertEqual(schedule.TURNOVER_SLOTS.count(slot),1)
                ledger = self.run_slot(slot)
                sources = ledger['sources']
                self.assertEqual(sources['0198']['observation_id'], sources['0186']['observation_id'])
                self.assertEqual(sources['0198']['collection_id'], 'ranking-reference')
                self.assertEqual(sources['0186']['run_id'], 'turnover-reference')
                self.assertEqual(sources['0186']['status'], 'partial')
                self.assertEqual(Path(sources['0186']['database']).name, slot.replace(':', '')+'.db')
            self.assertEqual(collect.call_count, 3)
            for call in collect.call_args_list:
                self.assertEqual(call.args[-1],1)
                self.assertGreaterEqual(call.args[1].request_interval,1.2)

    def test_failure_preserves_ranking_reference_and_does_not_use_previous_slot(self):
        previous = self.root/'data/turnover/2026-11-05/0940.db'
        previous.parent.mkdir(parents=True)
        previous.touch()  # File-presence check only; no fabricated market data.
        with patch('kiwoom_collector.operating_workflow.collect_turnover', side_effect=RuntimeError):
            ledger = self.run_slot('09:45')
        self.assertEqual(ledger['ranking_result'], 0)
        self.assertEqual(ledger['turnover']['status'], 'failed')
        self.assertEqual(self.workflow.review.call_args.args[-1], None)

    def test_existing_slot_is_preserved(self):
        path = self.root/'data/turnover/2026-11-05/0950.db'
        path.parent.mkdir(parents=True)
        path.touch()
        with patch('kiwoom_collector.operating_workflow.collect_turnover') as collect:
            ledger = self.run_slot('09:50')
        collect.assert_not_called()
        self.assertEqual(ledger['turnover']['status'], 'existing_attempt_preserved')
        self.assertEqual(self.workflow.review.call_args.args[-1], path)
