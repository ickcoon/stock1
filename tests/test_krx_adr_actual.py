"""Explicit opt-in actual KRX validation, independent SQL and manual window sums."""
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import unittest

from kiwoom_collector import krx_adr as aq


@unittest.skipUnless(os.environ.get('STOCK1_AQ_ACTUAL')=='1','explicit actual SQLite validation only')
class ActualAQTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output=Path(os.environ['STOCK1_AQ_OUTPUT']).resolve()
        cls.payload=json.loads((cls.output/'adr20.json').read_text(encoding='utf8'))
        cls.report=cls.payload['report'];cls.series=cls.payload['series']
        cls.bykey={(r['day'],r['market'],r['universe']):r for r in cls.series}
        cls.days=sorted({r['day'] for r in cls.series})
        # Independent SQL: do not call aggregate(), eligibility() or windowed().
        cls.sql={}
        with closing(aq.readonly(aq.SOURCE)) as db:
            eligible="""stock_code IS NOT NULL AND standard_code IS NOT NULL
              AND master_basis_date=day AND master_body_hash IS NOT NULL
              AND CAST(volume AS REAL)>0 AND CAST(open AS REAL)>0 AND CAST(high AS REAL)>0
              AND CAST(low AS REAL)>0 AND CAST(close AS REAL)>0
              AND COALESCE(is_new_listing,0)=0 AND change_price IS NOT NULL AND issues_json='[]'"""
            for universe,product in (
                ('common',"product_type='common_stock'"),
                ('common_unknown_reference',"(product_type='common_stock' OR (product_type='UNKNOWN' AND instr(COALESCE(name,''),'스팩')=0 AND instr(COALESCE(name,''),'기업인수목적')=0))")):
                query=f'''SELECT day,market,COUNT(*) AS eligible,
                SUM(CASE WHEN CAST(change_price AS REAL)>0 THEN 1 ELSE 0 END) AS up,
                SUM(CASE WHEN CAST(change_price AS REAL)<0 THEN 1 ELSE 0 END) AS down,
                SUM(CASE WHEN CAST(change_price AS REAL)=0 THEN 1 ELSE 0 END) AS flat
                FROM daily_latest WHERE ({product}) AND ({eligible}) GROUP BY day,market'''
                for row in db.execute(query):cls.sql[(row['day'],row['market'],universe)]=dict(row)

    def test_actual_shape_integrity_and_source_hash(self):
        r=self.report
        self.assertEqual((r['complete_sessions'],r['expected_sessions'],r['records']),(261,261,722666))
        self.assertEqual(r['source_integrity'],['ok'])
        self.assertEqual(r['duplicate_groups'],0)
        self.assertEqual(r['coverage_mismatches'],[])
        self.assertEqual(aq.sha(aq.SOURCE),r['source_sha256'])

    def test_every_daily_count_against_independent_sql(self):
        for key,expected in self.sql.items():
            actual=self.bykey[key]
            for k in ('eligible','up','down','flat'):self.assertEqual(actual[k],expected[k],(key,k))

    def test_every_window_dates_sums_and_decimal_value(self):
        for r in self.series:
            i=self.days.index(r['day']);days=self.days[max(0,i-19):i+1]
            self.assertEqual(r['window_dates'],days)
            self.assertEqual(r['window_start'],days[0])
            if i<19:
                self.assertIsNone(r['value']);continue
            parts=[self.bykey[(d,r['market'],r['universe'])] for d in days]
            a,b=(sum(x[k] for x in parts) for k in ('up','down'))
            self.assertEqual((r['up_sum'],r['down_sum']),(a,b))
            self.assertEqual(Decimal(r['value']),Decimal(100)*a/b)

    def test_combined_adds_markets_for_every_day(self):
        for d in self.days:
            for u in aq.UNIVERSES:
                combined=self.bykey[(d,'COMBINED',u)]
                for k in ('total','eligible','excluded','up','down','flat','unknown'):
                    self.assertEqual(combined[k],sum(self.bykey[(d,m,u)][k] for m in aq.MARKETS[:2]))

    def test_balances_unknown_reference_and_mismatch_policy(self):
        for r in self.series:
            self.assertEqual(r['total'],r['eligible']+r['excluded'])
            self.assertEqual(r['eligible'],r['up']+r['down']+r['flat'])
            self.assertEqual(sum(r['excluded_reasons'].values()),r['excluded'])
        for d in self.days:
            a,b=(self.bykey[(d,'COMBINED',u)] for u in aq.UNIVERSES)
            self.assertEqual(b['eligible']-a['eligible'],b['unknown_eligible'])
        self.assertEqual(self.report['reference_mismatches'],832)
        self.assertGreater(sum(r['mismatch_used'] for r in self.series if r['market']=='COMBINED' and r['universe']=='common'),0)

    def test_actual_null_and_calendar_coverage(self):
        self.assertEqual(self.report['missing_dates'],[])
        self.assertEqual(len(self.report['empty_dates']),22)
        self.assertEqual(self.report['valid_range'],['2025-11-04','2026-10-30'])
        self.assertEqual(self.report['valid_sessions'],242)
        for m in aq.MARKETS:
            for u in aq.UNIVERSES:
                series=[r for r in self.series if r['market']==m and r['universe']==u]
                self.assertEqual(sum(r['value'] is None for r in series),19)

    def test_saved_sqlite_matches_json(self):
        with closing(aq.readonly(self.output/'adr20.db')) as db:
            self.assertEqual(db.execute('pragma integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('select count(*) from adr20').fetchone()[0],261*6)
            self.assertEqual(db.execute('select count(*) from breadth_daily').fetchone()[0],261*6)
            for row in db.execute('select day,market,universe,value,status from adr20'):
                expected=self.bykey[(row['day'],row['market'],row['universe'])]
                self.assertEqual((row['value'],row['status']),(expected['value'],expected['status']))

    def test_manual_worked_examples_from_sql_counts(self):
        examples=[]
        for end in (self.days[19],self.days[130],self.days[-1]):
            i=self.days.index(end);days=self.days[i-19:i+1]
            for market in aq.MARKETS:
                daily=[]
                for d in days:
                    parts=[self.sql[(d,m,'common')] for m in aq.MARKETS[:2] if market in (m,'COMBINED')]
                    daily.append(dict(day=d,up=sum(x['up'] for x in parts),down=sum(x['down'] for x in parts)))
                up,down=(sum(x[k] for x in daily) for k in ('up','down'))
                value=str(Decimal(100)*up/down)
                self.assertEqual(value,self.bykey[(end,market,'common')]['value'])
                examples.append(dict(end=end,market=market,days=daily,sum_up=up,sum_down=down,
                                     formula=f'100 * {up} / {down}',result=value))
        (self.output/'manual-calculation.json').write_text(json.dumps(examples,indent=2),encoding='utf8')


if __name__=='__main__':unittest.main()
