"""AQ fixture tests: pure arithmetic, product policy, missing sessions and write guards."""
from copy import deepcopy
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
from html.parser import HTMLParser
import tempfile
import unittest

from kiwoom_collector import krx_adr as aq
from kiwoom_collector.krx_adr_chart import render


def row(day, **changes):
    r=dict(day=day,market='KOSPI',product_type='common_stock',name='fixture',stock_code='000001',
        standard_code='KR7000001000',master_basis_date=day,master_body_hash='fixture-master',volume='10',
        open='100',high='110',low='90',close='100',change_price='1',direction='up',is_new_listing=0,
        issues_json='[]',snapshot_id='fixture-'+day)
    r.update(changes)
    return r


class AQTests(unittest.TestCase):
    def test_ratio_of_sums_not_daily_average_and_first_19(self):
        days=[f'{i:02d}' for i in range(21)]
        counts={d:dict(up=1,down=1,flat=10,complete=True) for d in days}
        counts[days[19]].update(up=100,down=10)
        result=list(aq.windowed(days,counts))
        self.assertTrue(all(r['value'] is None for r in result[:19]))
        self.assertEqual(Decimal(result[19]['value']),Decimal(100)*119/29)
        self.assertEqual(result[20]['window_dates'],days[1:])
        self.assertEqual(result[20]['window_start'],days[1])

    def test_missing_session_blocks_windows_and_recovers(self):
        days=[f'{i:02d}' for i in range(45)]
        counts={d:dict(up=1,down=1,flat=0,complete=True) for d in days if d!=days[21]}
        result=list(aq.windowed(days,counts))
        self.assertTrue(all(r['value'] is None and r['status']=='missing_session' for r in result[21:41]))
        self.assertEqual(result[41]['value'],'100')
        self.assertIsNone(result[22]['up_sum'])

    def test_zero_denominator_and_incomplete(self):
        days=[f'{i:02d}' for i in range(20)]
        counts={d:dict(up=0,down=0,flat=10,complete=True) for d in days}
        self.assertEqual(list(aq.windowed(days,counts))[-1]['status'],'zero_declines')
        counts[days[10]]['complete']=False
        self.assertEqual(list(aq.windowed(days,counts))[-1]['status'],'missing_session')

    def test_unsorted_duplicate_sessions_rejected(self):
        for days in (['02','01'],['01','01']):
            with self.assertRaises(ValueError):list(aq.windowed(days,{}))

    def test_policy_unknown_products_flat_and_zero_volume(self):
        day='2026-10-30'
        records=[row(day),row(day,stock_code='000002',change_price='0',direction='flat'),
                 row(day,stock_code='000003',product_type='UNKNOWN'),
                 row(day,stock_code='000004',product_type='UNKNOWN',name='합성스팩'),
                 row(day,stock_code='000005',volume='0')]
        for i,p in enumerate(('preferred_stock','REIT','ETF','ETN','SPAC')):
            records.append(row(day,stock_code=f'{i+6:06d}',product_type=p))
        g,_=aq.aggregate(records,[day],{(day,'KOSPI')})
        base,ref=(g[(day,'KOSPI',u)] for u in aq.UNIVERSES)
        self.assertEqual((base['up'],base['down'],base['flat']),(1,0,1))
        self.assertEqual(ref['up'],2)
        self.assertEqual(base['unknown'],2)
        self.assertEqual(base['zero_volume'],1)
        self.assertEqual(base['total'],base['eligible']+base['excluded'])
        self.assertEqual(ref['excluded_reasons']['spac_hint'],1)
        self.assertFalse(g[(day,'COMBINED','common')]['complete'])

    def test_official_change_wins_over_simple_price_difference(self):
        days=['2026-10-29','2026-10-30']
        records=[row(days[0]),row(days[1],close='50',open='50',high='60',low='40',change_price='5')]
        g,anomalies=aq.aggregate(records,days,{(d,'KOSPI') for d in days})
        self.assertEqual(len(anomalies),1)
        self.assertEqual(g[(days[1],'KOSPI','common')]['up'],1)
        self.assertEqual(g[(days[1],'KOSPI','common')]['mismatch_used'],1)

    def test_combined_adds_counts_not_market_ratios(self):
        day='2026-10-30'
        records=[row(day),row(day,stock_code='000002',change_price='-1',direction='down'),
                 row(day,market='KOSDAQ',stock_code='000003',change_price='-1',direction='down')]
        g,_=aq.aggregate(records,[day],{(day,m) for m in aq.MARKETS[:2]})
        combined=g[(day,'COMBINED','common')]
        self.assertEqual((combined['up'],combined['down']),(1,2))
        self.assertTrue(combined['complete'])

    def test_missing_identity_new_listing_and_missing_change_excluded(self):
        for change,reason in ((dict(stock_code=None),'unverified_identity'),(dict(is_new_listing=1),'new_listing'),
                              (dict(change_price=None),'missing_change'),(dict(volume=None),'missing_volume')):
            self.assertEqual(aq.eligibility(row('2026-10-30',**change)),reason)

    def test_operational_paths_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            source=root/'operations.db';source.write_bytes(b'unchanged')
            with self.assertRaises(ValueError):aq.build(source,source,root/'output')
            self.assertEqual(source.read_bytes(),b'unchanged')
            self.assertFalse((root/'output').exists())
            with self.assertRaises(ValueError):aq.build(aq.SOURCE,aq.CHECKPOINT,source)
            self.assertEqual(source.read_bytes(),b'unchanged')

    def test_readonly_connection_rejects_database_write(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'fixture.db'
            with closing(sqlite3.connect(p)) as db,db:db.execute('create table fixture(n)')
            db=aq.readonly(p)
            try:
                with self.assertRaises(sqlite3.OperationalError):db.execute('insert into fixture values(1)')
            finally:db.close()

    def test_html_escapes_embedded_script_and_has_no_remote_dependency(self):
        document=render(dict(report='</script><script>alert(1)</script>',series=[]))
        self.assertNotIn('</script><script>alert(1)',document)
        self.assertIn('\\u003c/script',document)
        self.assertNotIn('src="https://',document)
        self.assertIn("r.value===null",document)
        class Options(HTMLParser):
            def __init__(self):
                super().__init__();self.values=[]
            def handle_starttag(self,tag,attrs):
                if tag=='option':self.values.append(dict(attrs)['value'])
        parser=Options();parser.feed(document)
        self.assertEqual(parser.values,['COMBINED','KOSPI','KOSDAQ'])


if __name__=='__main__':unittest.main()
