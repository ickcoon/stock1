"""Optional 2026-11-05 saved-data validation, read-only and network forbidden."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_preview

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config/c15-preview.json'


@unittest.skipUnless(CONFIG.is_file(), 'local C15 saved-source config unavailable')
class ActualLeadershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
        config['turnover_fallback']=False  # Preserve the C15-7/8/9/10 exact-slot baseline.
        if not Path(config['runtime_root']).is_dir():
            raise unittest.SkipTest('saved runtime unavailable')
        connect=sqlite3.connect
        def readonly(database, *args, **kwargs):
            if '?mode=ro' not in str(database) or kwargs.get('uri') is not True:
                raise AssertionError('all actual SQLite connections must be read-only URI')
            return connect(database,*args,**kwargs)
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')), patch('sqlite3.connect',side_effect=readonly):
            cls.results={s:c15_preview.generate(config,day='2026-11-05',slot=s) for s in c15_preview.SLOTS}

    def test_actual_rising_only_and_unknown_eligibility_held(self):
        for slot in ('09:40','09:50'):
            result=self.results[slot]
            self.assertEqual(result['raw_counts'],{'0198':20,'0186':100})
            v=result['morning_leadership']
            self.assertEqual(len(v['analysis']['sectors']),3)
            for sector in v['analysis']['sectors']:
                self.assertGreaterEqual(sector['rising_count'],2)
                for s in sector['leaders']:
                    self.assertGreater(float(s['raw_change']),0)
                    self.assertEqual(s['capital_status'],'unverified')
                    self.assertEqual(s['eligibility_status'],'pending')
            self.assertEqual(v['analysis']['confirmed_sectors'],[])
            self.assertFalse(result['telegram_sent'])
            self.assertEqual(result['api_calls'],0)

    def test_actual_0945_missing_is_not_replaced(self):
        result=self.results['09:45']
        self.assertEqual(result['raw_counts']['0186'],0)
        self.assertEqual(result['morning_leadership']['analysis']['sectors'],[])
        self.assertEqual(result['morning_leadership']['data_status'],'미확인')

    def test_actual_mapping_denominators_and_telegram_limits(self):
        for slot,linked,total in [('09:40',50,103),('09:45',14,20),('09:50',52,105)]:
            r=self.results[slot]
            c=r['morning_leadership']['mapping']['coverage']
            self.assertEqual((c['theme_linked'],c['total']),(linked,total))
            self.assertEqual(c['code_mapped'],0)
            self.assertEqual(''.join(r['telegram_preview_messages']),r['telegram_preview_text'])
            self.assertTrue(all(len(s.encode('utf-16-le'))//2<=4096 for s in r['telegram_preview_messages']))

    def test_c15_7_preview_text_regression(self):
        baseline=ROOT/'diagnostics/c15-8-baseline-0940/preview.json'
        if not baseline.exists(): self.skipTest('C15-7 baseline artifact unavailable')
        before=json.loads(baseline.read_text(encoding='utf8'))
        self.assertEqual(self.results['09:40']['text'].split('\n\n[ARVIS 오전 주도주 분석]')[0],before['text'])


if __name__=='__main__': unittest.main()
