import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_collector import c12_latest as latest, c12_sector_audit as audit
from test_c12_latest import snapshot,stamp


class SectorAuditTests(unittest.TestCase):
    def test_coarse_priority_multitag_and_no_guess(self):
        report=latest.analyze([snapshot()],as_of=stamp('09:50:02'),environment='real')
        tags=[dict(stock_code='000001',sector_name=s) for s in ['반도체','반도체 소부장','반도체']]
        tags += [dict(stock_code='000002',sector_name=s) for s in ['로봇','조선']]
        tags += [dict(stock_code='000003',sector_name='양자보안')]
        result=audit.analyze(report,dict(as_of=report['as_of'],tags=tags))
        self.assertEqual(result['coverage']['coarse_mapped'],1)
        self.assertEqual(result['coverage']['ambiguous'],1)
        self.assertEqual(result['attention_top3'][0]['distinct_stocks'],1)
        self.assertEqual(result['connections'][0]['stock_code'],'000001')
        self.assertEqual(result['connections'][2]['state'],'fine_only')
        self.assertIn('000003',result['unclassified_stock_codes'])
        self.assertEqual(result['reported_rising_top3'],[])
        self.assertEqual(result['candidate_selection_status'],report['candidate_selection_status'])

    def test_existing_alias_and_reported_risers(self):
        turn=snapshot('0186','09:40:00','09:40:02')
        turn['rows'].append(dict(turn['rows'][0],stock_code='000002',turnover_rank=2,
            raw_json=turn['rows'][0]['raw_json'].replace('000001','000002')))
        turn['snapshot_sha256']=latest.digest({k:v for k,v in turn.items() if k!='snapshot_sha256'})
        report=latest.analyze([snapshot(),turn],as_of=stamp('09:50:02'),environment='real')
        result=audit.analyze(report,dict(as_of=report['as_of'],tags=[dict(stock_code=c,sector_name='원전') for c in ['000001','000002']]))
        self.assertEqual(result['attention_top3'][0]['name'],'원전·전력')
        self.assertEqual(result['reported_rising_top3'][0]['reported_positive_count'],2)
        self.assertFalse(result['candidate_confirmed'])

    def test_readonly_history_future_edits_and_untag(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'old.db';db=sqlite3.connect(p)
            db.executescript('CREATE TABLE sectors(sector_id,sector_name);CREATE TABLE stock_sectors(stock_code);CREATE TABLE stock_sector_events(event_id,stock_code,sector_id,action,source,changed_at);')
            db.execute('insert into sectors values(1,?)',('반도체',))
            for i,action,at in [(1,'tag','09:20:00'),(2,'untag','09:40:00'),(3,'tag','09:55:00')]:
                db.execute('insert into stock_sector_events values(?,?,?,?,?,?)',(i,'000001',1,action,'manual',stamp(at)))
            db.commit();db.close();before=p.read_bytes()
            self.assertEqual(audit.read_tags(p,as_of=stamp('09:30:00'))['tags'][0]['stock_code'],'000001')
            self.assertEqual(audit.read_tags(p,as_of=stamp('09:50:00'))['tags'],[])
            self.assertEqual(p.read_bytes(),before)


if __name__=='__main__':unittest.main()
