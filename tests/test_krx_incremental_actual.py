"""Explicit opt-in read-only checks on AS actual data; never calls the network."""
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import unittest
from kiwoom_collector import krx_incremental as inc, krx_adr as adr


@unittest.skipUnless(os.environ.get('STOCK1_AS_ACTUAL')=='1','opt-in actual SQLite validation')
class ActualIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pointer=json.loads((inc.ROOT/'latest-adr.json').read_text(encoding='utf8'))
        cls.output=Path(pointer['output'])
        cls.payload=json.loads((cls.output/'adr20.json').read_text(encoding='utf8'))
        cls.latest=cls.payload['report']['latest']

    def test_integrity_and_foreign_keys(self):
        self.assertEqual(inc.integrity(inc.DATABASE),dict(integrity_check=['ok'],foreign_key_violations=[]))

    def test_original_history_keys_and_market_pointers_retained(self):
        with closing(adr.readonly(inc.DATABASE)) as db:
            db.execute('ATTACH DATABASE ? AS seed',(inc.SEED.as_uri()+'?mode=ro',))
            missing=db.execute('''SELECT count(*) FROM seed.bars s LEFT JOIN main.bars b
              ON s.snapshot_id=b.snapshot_id AND s.day=b.day AND s.market=b.market
              AND s.official_code_raw=b.official_code_raw WHERE b.snapshot_id IS NULL''').fetchone()[0]
            changed=db.execute('''SELECT count(*) FROM seed.market_days s LEFT JOIN main.market_days b
              ON s.day=b.day AND s.market=b.market WHERE b.day IS NULL OR
              COALESCE(s.current_snapshot,'')!=COALESCE(b.current_snapshot,'')''').fetchone()[0]
        self.assertEqual((missing,changed),(0,0))

    def test_actual_new_days_and_live_origin(self):
        with closing(adr.readonly(inc.DATABASE)) as db:
            for day in ('2026-11-02','2026-11-03'):
                rows=db.execute('SELECT market,count(*),count(distinct official_code_raw),min(day),max(day) '
                    'FROM daily_latest WHERE day=? GROUP BY market',(day,)).fetchall()
                self.assertEqual({r[0] for r in rows},set(inc.MARKETS))
                self.assertTrue(all(r[1]==r[2]>0 and r[3]==r[4]==day for r in rows))
            self.assertEqual(db.execute("SELECT count(*) FROM snapshots WHERE source_kind!='krx_live'").fetchone()[0],0)

    def test_latest_twenty_day_sums_with_independent_sql(self):
        days=sorted({r['day'] for r in self.payload['series']})[-20:]
        self.assertEqual(len(days),20)
        predicate="""product_type='common_stock' AND stock_code IS NOT NULL AND standard_code IS NOT NULL
          AND master_basis_date=day AND master_body_hash IS NOT NULL AND CAST(volume AS REAL)>0
          AND CAST(open AS REAL)>0 AND CAST(high AS REAL)>0 AND CAST(low AS REAL)>0 AND CAST(close AS REAL)>0
          AND COALESCE(is_new_listing,0)=0 AND change_price IS NOT NULL AND issues_json='[]'"""
        sums={}
        with closing(adr.readonly(inc.DATABASE)) as db:
            for market in inc.MARKETS:
                row=db.execute('SELECT sum(CASE WHEN CAST(change_price AS REAL)>0 THEN 1 ELSE 0 END),'
                    'sum(CASE WHEN CAST(change_price AS REAL)<0 THEN 1 ELSE 0 END) FROM daily_latest '
                    'WHERE day IN ('+','.join('?' for _ in days)+') AND market=? AND '+predicate,(*days,market)).fetchone()
                sums[market]=tuple(row)
        sums['COMBINED']=tuple(sum(sums[m][i] for m in inc.MARKETS) for i in (0,1))
        for market,(up,down) in sums.items():
            latest=self.latest[market]['common']
            self.assertEqual((latest['up_sum'],latest['down_sum']),(up,down))
            self.assertEqual(Decimal(latest['value']),Decimal(100)*up/down)

    def test_html_and_window_end(self):
        document=(self.output/'adr20.html').read_text(encoding='utf8')
        self.assertIn('<svg',document)
        self.assertIn('2026-11-03',document)
        for market in (*inc.MARKETS,'COMBINED'):
            self.assertEqual(self.latest[market]['common']['day'],'2026-11-03')
            self.assertEqual(self.latest[market]['common']['window_sessions'],20)


if __name__=='__main__':unittest.main()
