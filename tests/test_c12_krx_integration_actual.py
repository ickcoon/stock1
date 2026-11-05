"""Opt-in saved SQLite/reference tests. No live Kiwoom or KRX transport."""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import unittest

from kiwoom_collector import c12_krx_integration as ar
from kiwoom_collector.krx_adr import sha


@unittest.skipUnless(os.environ.get('STOCK1_AR1_ACTUAL')=='1','explicit stored DB checks only')
class ActualIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=ar.ROOT;cls.output=cls.root/'20261104-actual'
        cls.bundle=json.loads((cls.output/'integration.json').read_text(encoding='utf8'))
        cls.reference=ar.references(ar.MASTER)

    def test_observed_codes_match_independent_official_lookup(self):
        self.assertEqual(self.bundle['counts']['observed'],34)
        self.assertEqual(self.bundle['counts']['code_matched'],33)
        with ar.read_db(ar.MASTER) as db:
            for r in self.bundle['rows']:
                official=db.execute('select * from krx_securities where stock_code=?',(r['stock_code'],)).fetchone()
                if official:
                    for k in ('stock_code','market','official_name','abbreviated_name','product_type','standard_code'):
                        self.assertEqual(r['official'][k],official[k])
                else:
                    self.assertTrue(r['retained_in_attention']);self.assertIsNone(r['official'])

    def test_master_and_confirmed_mapping_denominators(self):
        self.assertEqual(len(self.reference['securities']),2767)
        self.assertEqual(self.reference['confirmed_names'],514)
        self.assertEqual(self.reference['confirmed_relations'],773)
        self.assertEqual(self.bundle['counts']['theme_mapped'],26)
        self.assertEqual(self.bundle['counts']['multi_theme'],11)

    def test_every_raw_theme_relation_and_source_json_preserved(self):
        with ar.read_db(ar.MASTER) as db:
            for r in self.bundle['rows']:
                for t in r['theme_references']:
                    row=db.execute('select source_json,stock_code from external_theme_relations where raw_name=? and raw_theme=?',(t['raw_name'],t['raw_theme'])).fetchone()
                    self.assertEqual(t['original_relation'],json.loads(row['source_json']))
                    self.assertEqual(row['stock_code'],r['stock_code'])
                    self.assertFalse(t['historical_membership_verified'])

    def test_original_c12_report_and_html_are_identical(self):
        baseline=json.loads((self.root/'c12-existing-analysis.json').read_text(encoding='utf8'))
        self.assertEqual(baseline,self.bundle['c12']['existing_c12_report'])
        self.assertEqual(baseline['candidate_selection_status'],'held')
        base=(self.root/'actual-base.html').read_text(encoding='utf8')
        integrated=(self.output/'dashboard.html').read_text(encoding='utf8')
        self.assertEqual(integrated.replace(ar.CSS,'',1).replace(ar.section(self.bundle),'',1),base)

    def test_aq_database_and_standalone_chart_exact_match(self):
        with ar.read_db(ar.AQ/'adr20.db') as db:
            for market,values in self.bundle['adr']['latest'].items():
                value=values['common']
                actual=db.execute('select value,status from adr20 where day=? and market=? and universe=?',(value['day'],market,'common')).fetchone()
                self.assertEqual((value['value'],value['status']),tuple(actual))
        self.assertEqual(sha(ar.AQ/'adr20.html'),sha(self.output/'adr20.html'))
        self.assertEqual(round(Decimal(self.bundle['adr']['latest']['COMBINED']['common']['value']),4),Decimal('68.8935'))

    def test_no_original_sources_changed(self):
        before=json.loads((self.root/'protected-before.json').read_text(encoding='utf8'))
        for name,value in before.items():self.assertEqual(sha(Path(name)),value,name)
        actual=json.loads((self.root/'actual-validation.json').read_text(encoding='utf8'))
        self.assertEqual(sha(actual['source']),actual['source_sha256'])

    def test_existing_demo_and_its_core_top5_preserved(self):
        original=Path('C:/Users/Public/Documents/ESTsoft/CreatorTemp/stock1-permanent-dev/night-20261101/integration/diagnostics/daily_chart/c12l-arvis-demo.html')
        folder=self.root/'20261104-demo-preserved'
        bundle=json.loads((folder/'integration.json').read_text(encoding='utf8'))
        document=(folder/'dashboard.html').read_text(encoding='utf8')
        self.assertEqual(document.replace(ar.CSS,'',1).replace(ar.section(bundle),'',1),original.read_text(encoding='utf8'))
        self.assertEqual(bundle['c12']['source_kind'],'fixture_existing_demo_top5')

    def test_historical_mock_and_missing_ak_are_not_relabelled_real(self):
        self.assertEqual(self.bundle['environment'],'mock')
        self.assertFalse(self.bundle['c12']['actual_0186_available'])
        self.assertIsNone(self.bundle['ak_comparison']['new_mapped'])
        self.assertEqual(self.bundle['adr']['date_relation'],'after_analysis_reference_only')
        self.assertEqual(self.bundle['adr']['basis_date'],'2026-10-30')
        self.assertEqual(self.bundle['analysis_day'],'2026-10-20')


if __name__=='__main__':unittest.main()
