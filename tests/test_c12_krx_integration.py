"""AR-1 fixtures: code/theme/date overlays never replace existing C12 decisions."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from kiwoom_collector import c12_krx_integration as ar, am_momentum


def security(code='005930',kind='common_stock'):
    return dict(stock_code=code,standard_code='KR7'+code+'000',market='KOSPI',official_name='공식명'+code,
        abbreviated_name='약명'+code,product_type=kind,type_verified=kind!='UNKNOWN',
        basis_date='2026-10-30',listing_date='2000-01-01',classification_json='{}')


def reference():
    stocks=[security(),security('005935','preferred_stock'),security('000660')]
    themes=[dict(raw_name='원문',raw_theme=t,source_kind='external_trader_close',
                 original_relation=dict(evidence_sources=['fixture.png']),historical_membership_verified=False)
            for t in ('반도체','반도체 관련주')]
    return dict(securities={r['stock_code']:r for r in stocks},themes={'005930':themes},
                basis_date='2026-10-30',confirmed_names=2,confirmed_relations=2)


def document(day='2026-11-04'):
    return dict(as_of=day+'T09:50:02+09:00',source_kind='fixture',environment='mock',
        observations=dict(watchlist=[dict(stock_code='005930',name='원문 이름',api='0198'),
                                    dict(stock_code='005935',name='우선주',api='0186'),
                                    dict(stock_code='999999',name='미매칭',api='0198')]),
        analysis=dict(rows=[dict(stock_code='999999',candidate_status='attention_leader')]),
        candidate_filter=dict(decisions={'005935':dict(state='analysis_candidate')}))


def adr():
    latest={m:dict(common=dict(day='2026-10-30',value='68.89345296004035599754981443',status='provisional')) for m in ('KOSPI','KOSDAQ','COMBINED')}
    series=[dict(day='2026-10-30',market=m,universe='common',value='68.89345296004035599754981443',status='provisional') for m in latest]
    return dict(report=dict(latest=latest,policy='fixture'),series=series)


class IntegrationTests(unittest.TestCase):
    def test_unmatched_retained_and_existing_decisions_unchanged(self):
        doc=document();before=deepcopy(doc);result=ar.enrich(doc,reference(),adr())
        self.assertEqual(doc,before);self.assertEqual(result['c12'],before)
        self.assertEqual(result['counts']['observed'],3)
        self.assertEqual(result['counts']['code_matched'],2)
        r=result['rows'][-1]
        self.assertEqual(r['identity_status'],'unmatched');self.assertTrue(r['retained_in_attention'])
        self.assertEqual(result['c12']['candidate_filter'],before['candidate_filter'])

    def test_0198_and_0186_reuse_code_normalization_without_changing_input(self):
        doc=dict(as_of='2026-11-04T09:50:00+09:00',source_kind='fixture',environment='mock',
                 rankings=[dict(stocks=[dict(stk_cd='5930',rank=1)])],
                 source_records=[dict(kind='turnover',stock_code=660,turnover_rank=1)])
        result=ar.enrich(doc,reference())
        self.assertEqual([r['stock_code'] for r in result['rows']],['005930','000660'])
        self.assertEqual(doc['rankings'][0]['stocks'][0]['stk_cd'],'5930')
        self.assertEqual(result['counts']['code_matched'],2)

    def test_invalid_code_not_replaced_by_name_or_deleted(self):
        doc=document();doc['observations']['watchlist']=[dict(stock_code='A005930',name='공식명005930')]
        result=ar.enrich(doc,reference())
        self.assertEqual(result['counts']['unmatched'],1)
        self.assertIsNone(result['rows'][0]['stock_code'])

    def test_multiple_raw_themes_and_provenance_not_merged(self):
        ref=reference();before=deepcopy(ref)
        result=ar.enrich(document(),ref)
        self.assertEqual(result['rows'][0]['raw_theme_labels'],['반도체','반도체 관련주'])
        self.assertEqual(result['rows'][0]['theme_references'],ref['themes']['005930'])
        self.assertEqual(ref,before)

    def test_product_reference_is_separate_and_filters_each_type(self):
        for kind in ('ETF','ETN','REIT','SPAC','preferred_stock','mutual_fund','infrastructure_product'):
            self.assertEqual(ar.product_reference(security(kind=kind))['state'],'excluded')
        self.assertEqual(ar.product_reference(security(kind='UNKNOWN'))['state'],'pending')
        self.assertEqual(ar.product_reference(security())['state'],'common_reference')
        self.assertEqual(ar.product_reference(None)['state'],'unmatched')
        self.assertEqual(ar.product_reference(security())['trade_eligibility'],'unchanged')

    def test_adr_is_exact_and_old_date_explicit(self):
        result=ar.enrich(document(),reference(),adr())
        self.assertEqual(result['adr']['latest'],adr()['report']['latest'])
        self.assertEqual(result['adr']['date_relation'],'older_than_analysis')
        html=ar.section(result)
        self.assertIn('2026-10-30',html);self.assertIn('2026-11-04',html)
        self.assertIn('오늘 실시간 값 아님',html)

    def test_future_reference_is_not_point_in_time_input(self):
        result=ar.enrich(document('2026-10-20'),reference(),adr())
        self.assertEqual(result['adr']['date_relation'],'after_analysis_reference_only')
        self.assertEqual(result['master_time_relation'],'after_analysis_reference_only')
        self.assertIsNone(result['adr']['asof_analysis']['COMBINED'])
        self.assertIn('당시 의사결정에 사용 불가',ar.section(result))

    def test_missing_adr_has_notice_and_no_broken_iframe(self):
        result=ar.enrich(document(),reference())
        html=ar.section(result)
        self.assertIn('ADR 자료 없음',html);self.assertNotIn('id="adr-chart"',html)
        with tempfile.TemporaryDirectory() as folder:self.assertIsNone(ar.load_adr(folder))

    def test_dashboard_body_and_core_section_roundtrip(self):
        base='<html><head></head><body><main><h1>주도섹터 TOP3</h1><div id="core">기존 핵심 종목</div></main></body></html>'
        bundle=ar.enrich(document(),reference(),adr())
        result=ar.attach_dashboard(base,bundle)
        self.assertEqual(result.replace(ar.CSS,'',1).replace(ar.section(bundle),'',1),base)
        self.assertIn('sandbox="allow-scripts"',result)
        self.assertNotIn('allow-scripts allow-same-origin',result)

    def test_existing_am_engine_result_is_preserved(self):
        doc=json.loads((Path(__file__).parent/'fixtures/am_momentum_synthetic.json').read_text(encoding='utf8'))
        baseline=am_momentum.analyze(doc,generated_at='2026-10-29T10:00:00+09:00')
        wrapped=dict(as_of=baseline['selection_cutoff_at'],source_kind='fixture',environment='mock',analysis=baseline)
        result=ar.enrich(wrapped,reference())
        self.assertEqual(result['c12']['analysis'],baseline)
        self.assertEqual(am_momentum.analyze(doc,generated_at='2026-10-29T10:00:00+09:00'),baseline)

    def test_operational_output_rejected_before_opening_master(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'operations.db';p.write_bytes(b'unchanged')
            with self.assertRaises(ValueError):ar.export(document(),'<main></main>',p,master=Path('missing'))
            self.assertEqual(p.read_bytes(),b'unchanged')


if __name__=='__main__':unittest.main()
