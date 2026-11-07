import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from kiwoom_collector import c15_leadership as leadership,c15_delivery as delivery,telegram_briefing as tg
from kiwoom_collector.am_time_policy import instant


class PMReferenceTests(unittest.TestCase):
    def reference(self):
        pair=dict(ranking=dict(rows=[dict(stk_cd='005380',rank=1)]),turnover=dict(rows=[
            dict(stk_cd='005380',stk_nm='현대차',flu_rt='3.2',trde_prica='120000',now_rank='10'),
            dict(stk_cd='000270',stk_nm='기아',flu_rt='2.0',trde_prica='70000',now_rank='50')]))
        tags=[dict(stock_code=code,sector_name='자동차',source='test_saved_mapping') for code in ('005380','000270')]
        with patch('kiwoom_collector.c15_identity.load_official',return_value=([],dict(basis_date='2026-11-04'))),patch('kiwoom_collector.c15_briefing_data.product_evidence',return_value={}),patch('kiwoom_collector.c15_briefing_data.practical_product',return_value=dict(verified=True,excluded=False)),patch('kiwoom_collector.c15_briefing_data.capital_reference',return_value=dict(won=None,basis_date=None)),patch('kiwoom_collector.cross_adapter.analyze_pair',return_value=dict(excluded_turnover=[dict(stock_code='000270')],review=dict(turnover=[]))),patch('kiwoom_collector.c12_external_theme_bridge.load',return_value=dict(records=[])),patch.object(leadership,'map_themes',return_value=(tags,{})):
            return leadership.reference_afternoon(pair,as_of='2026-11-05T14:30:00+09:00')

    def data(self,slot='14:30'):
        return dict(day='2026-11-05',analysis_at='2026-11-05T'+slot+':00+09:00',reference=self.reference())

    def test_original_c15_score_and_reference_only_not_order_eligibility(self):
        r=self.reference()
        self.assertEqual(r['sectors'][0]['rising_count'],2)
        self.assertEqual([s['stock_name'] for s in r['candidates']],['현대차','기아'])
        self.assertTrue(r['candidates'][0]['closing_numeric_pass'])
        self.assertFalse(r['candidates'][1]['closing_numeric_pass'])
        self.assertTrue(all(s['market_cap_won_reference'] is None for s in r['candidates']))
        self.assertFalse(r['execution_allowed']);self.assertFalse(r['candidate_confirmed'])

    def test_telegram_generation_validation_and_separate_pm_slots(self):
        first=delivery.wrap_pm(self.data());second=delivery.wrap_pm(self.data('15:00'))
        tg.validate(first);text=tg.summary(first)
        self.assertIn('참고용 분석',text);self.assertIn('현대차',text)
        self.assertIn('1,200억',text);self.assertIn('자동차',text)
        self.assertNotEqual(tg.delivery_schema(first),tg.delivery_schema(second))
        first['reference']['execution_allowed']=True
        with self.assertRaises(ValueError):tg.validate(first)

    def test_disabled_replay_and_message_generation_failures_never_send(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'report';out.mkdir()
            due=instant('2026-11-05T14:30:00+09:00')
            workflow=SimpleNamespace(root=root,settings=dict(telegram_send_enabled=False),clock=lambda:due+timedelta(seconds=20))
            with patch('kiwoom_collector.c16_sessions.replay_afternoon',return_value=self.data()),patch.object(tg,'deliver') as send:
                first=delivery.process_pm(workflow,due,ranking_path=root/'saved.db',turnover_path=root/'turn.db',folder=out)
                second=delivery.process_pm(workflow,due,ranking_path=root/'saved.db',turnover_path=root/'turn.db',folder=out)
            self.assertEqual(first['delivery']['status'],'disabled')
            self.assertEqual(second['delivery']['status'],'frozen_replay_no_send');send.assert_not_called()
            other=root/'failure';other.mkdir()
            with patch('kiwoom_collector.c16_sessions.replay_afternoon',side_effect=ValueError),patch.object(tg,'deliver') as send:
                result=delivery.process_pm(workflow,due,ranking_path=root/'saved.db',turnover_path=root/'turn.db',folder=other)
            self.assertEqual(result['status'],'message_generation_failed');send.assert_not_called()

    def test_send_failure_is_distinct_from_generation_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'report';out.mkdir();due=instant('2026-11-05T14:30:00+09:00')
            workflow=SimpleNamespace(root=root,settings=dict(telegram_send_enabled=True,notification_state=str(root/'state')),clock=lambda:due+timedelta(seconds=20))
            with patch('kiwoom_collector.c16_sessions.replay_afternoon',return_value=self.data()),patch.object(tg.Configuration,'from_environment',return_value=object()),patch.object(tg,'deliver',side_effect=RuntimeError):
                result=delivery.process_pm(workflow,due,ranking_path=root/'saved.db',turnover_path=root/'turn.db',folder=out)
            self.assertEqual(result['status'],'reviewed');self.assertEqual(result['delivery']['status'],'send_failed')
