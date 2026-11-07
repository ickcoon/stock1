"""Focused saved-preview checks; fixtures are explicitly synthetic."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_preview as p, am_briefing
from test_c12_latest import snapshot


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_exact_slot_never_pairs_previous_turnover(self):
        rank=snapshot();rank['scheduled_at']='2026-11-03T09:50:00+09:00'
        turn=snapshot('0186',clock='09:40:00')
        paired,ranks,turns=p.exact_slot([rank,turn],'2026-11-03','09:50')
        self.assertEqual(len(ranks),1)
        self.assertEqual(turns,[])
        self.assertEqual([s['api'] for s in paired],['0198'])

    def test_afternoon_and_wrong_day_do_not_become_morning(self):
        rank=snapshot(clock='13:30:00',end='13:30:01')
        rank['scheduled_at']='2026-11-03T09:40:00+09:00'
        turn=snapshot('0186',clock='11:00:00',end='11:00:01')
        self.assertEqual(p.exact_slot([rank,turn],'2026-11-03','09:40'),([],[],[]))
        self.assertEqual(p.exact_slot([snapshot()],'2026-11-04','09:50'),([],[],[]))

    def test_live_reader_cannot_write_or_create_missing_db(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.db'
            db=sqlite3.connect(path);db.execute('create table evidence(id integer)');db.close()
            with p.latest.read_db(path) as reader:
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute('insert into evidence values (1)')
            missing=Path(folder)/'missing.db'
            with self.assertRaises(FileNotFoundError):
                with p.latest.read_db(missing):pass
            self.assertFalse(missing.exists())

    def test_three_sectors_format_is_synthetic_only(self):
        doc=json.loads((Path(__file__).parent/'fixtures/am_sector_briefing_synthetic.json').read_text(encoding='utf8'))
        report=am_briefing.analyze(doc)
        text=am_briefing.format_mobile(report)
        self.assertEqual(len(report['sectors']),3)
        self.assertTrue(all(len(s['leaders'])==3 for s in report['sectors']))
        self.assertIn('합성 테스트',text)
        self.assertIn('조회 ',text)

    def test_partial_and_unknown_stay_explicit(self):
        # No candidate rows or fabricated prices: test missing-output policy only.
        selection={'selected':{api:{'status':'missing'} for api in ('0198','0186')}}
        data=dict(reference_at='2026-11-03T09:45:00+09:00',raw_times={'0198':[],'0186':[]},
            raw_counts={'0198':0,'0186':0},raw_turnover_status=[{'status':'partial','rows':100}],
            analysis={'snapshot_selection':selection,'sectors':[]},saved_observations=[],
            theme_references={},auxiliary={},warnings=[])
        text=p.format_preview(data)
        self.assertIn('전체 시장 순위로 해석하지 않음',text)
        self.assertIn('미확인',text)
        self.assertNotIn('0.00%',text)
