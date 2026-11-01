"""Synthetic C12 published order and C14 joins; network forbidden."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from kiwoom_collector import am_candidate_adapter,am_briefing
from kiwoom_collector.c12_chart_bridge import published_top,integrate
from kiwoom_collector.analyst.card import chart_card
from kiwoom_collector.analyst.context import analyze_context
from kiwoom_collector.daily_chart import fingerprint
from test_am_candidate_adapter import master,AT,NOW
from test_am_briefing import FIXTURE
from test_weekly_hybrid import weekly_fixture


def c12_fixture():
    doc=deepcopy(FIXTURE);doc['selection_cutoff_at']=AT
    return am_briefing.analyze(am_candidate_adapter.join(doc,master()),generated_at=NOW)


class ChartBridgeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.report=c12_fixture();self.selected=published_top(self.report)

    def card(self,code,png=True):
        doc,_,_=weekly_fixture();doc['stock_code']=code
        card=chart_card(analyze_context(doc));folder=self.root/code;folder.mkdir(exist_ok=True)
        if png:
            from PIL import Image
            Image.new('RGB',(10,10),'white').save(folder/'source.png')
            card.update(mini_chart_status='available',mini_chart_path='source.png')
            card.pop('card_reference');card['card_reference']=fingerprint(card)
        path=folder/'card.json';path.write_text(json.dumps(card),encoding='utf-8')
        return dict(card_json=str(path)),card

    def test_rank_filter_and_code_string_preserved(self):
        expected=[]
        for sector in self.report['sectors']:
            for leader in sector['leaders']:expected.append(leader['stock_code'])
        self.assertEqual([x['stock_code'] for x in self.selected],expected[:5])
        self.assertTrue(all(type(x['stock_code']) is str and len(x['stock_code'])==6 for x in self.selected))
        self.assertFalse({'000001','000002','000003','000004'} & {x['stock_code'] for x in self.selected})
        self.assertEqual(len(published_top(self.report,3)),3)

    def test_saved_card_png_copy_backward_compatibility(self):
        code=self.selected[0]['stock_code'];artifact,card=self.card(code)
        before=deepcopy(self.report)
        result=integrate(self.report,{code:artifact},self.root/'out',generated_at=NOW)
        item=result['items'][0];self.assertEqual(item['status'],'available')
        linked=json.loads((self.root/'out'/item['card_path']).read_text(encoding='utf-8'))
        for key,value in card.items():
            if key not in ('card_reference','mini_chart_path'):self.assertEqual(linked[key],value)
        self.assertTrue((self.root/'out'/item['mini_chart_path']).is_file())
        self.assertEqual(result['api_calls'],0);self.assertEqual(self.report,before)

    def test_missing_chart_and_bad_card_are_independent(self):
        a,b,c=[r['stock_code'] for r in self.selected[:3]]
        art_a,_=self.card(a,png=False);art_b,_=self.card(b)
        path=Path(art_b['card_json']);value=json.loads(path.read_text());value['stock_code']='005930';path.write_text(json.dumps(value))
        art_c,_=self.card(c)
        result=integrate(self.report,{a:art_a,b:art_b,c:art_c},self.root/'mixed',generated_at=NOW)
        self.assertEqual([r['status'] for r in result['items'][:3]],['partial','failed','available'])
        self.assertEqual(result['items'][3]['status'],'unavailable')

    def test_failed_renderer_keeps_next_candidate(self):
        a,b=[r['stock_code'] for r in self.selected[:2]]
        art_a,_=self.card(a);art_b,_=self.card(b)
        art_a['diagnostic_json']=[str(self.root/'absent.json')]
        result=integrate(self.report,{a:art_a,b:art_b},self.root/'continue',generated_at=NOW)
        self.assertEqual(result['items'][0]['status'],'failed')
        self.assertEqual(result['items'][1]['status'],'available')

    def test_generates_png_from_existing_daily_adapter_and_card(self):
        from test_analyst_saved import archive
        from kiwoom_collector.analyst.saved import diagnostic_pages
        from kiwoom_collector.analyst.observed_daily import analyze_observed_daily
        from test_analyst_chart import ASOF
        code=self.selected[0]['stock_code'];saved,doc=archive()
        saved=json.loads(json.dumps(saved).replace('005930',code).replace('ka11081','ka10081'))
        item=saved['results'][0];item['response_projection']['stk_dt_pole_chart_qry']=item['response_projection'].pop('result_list')
        doc['stock_code']=code;doc['price_basis']['verified']=False
        observed=analyze_observed_daily(diagnostic_pages(saved)[0],as_of=ASOF,data_origin='synthetic_fixture')
        mapping={}
        for key,value in (('report_json',analyze_context(doc)),('observed_json',observed),('diagnostic',saved)):
            path=self.root/(key+'.json');path.write_text(json.dumps(value),encoding='utf-8');mapping[key]=str(path)
        mapping['diagnostic_json']=[mapping.pop('diagnostic')]
        result=integrate(self.report,{code:mapping},self.root/'generated',generated_at=NOW)
        self.assertEqual(result['items'][0]['status'],'available')
        png=self.root/'generated'/result['items'][0]['mini_chart_path']
        self.assertEqual(png.read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
        card=json.loads((self.root/'generated'/result['items'][0]['card_path']).read_text(encoding='utf-8'))
        self.assertEqual(card['mini_chart_metadata']['candle_count'],60)
        self.assertEqual(card['moving_averages']['MA20']['status'],'provisional')

    def test_fewer_candidates_not_filled_with_excluded_stocks(self):
        doc=deepcopy(self.report['input_document']);keep={x['stock_code'] for x in self.selected[:2]}
        for code,security in doc['securities'].items():
            if code not in keep:
                for metric in security.get('market_cap',[]):metric['value']='1'
        report=am_briefing.analyze(doc,generated_at=NOW)
        self.assertEqual(len(published_top(report)),2)

    def test_no_policy_free_or_tampered_candidates(self):
        bad=deepcopy(self.report);bad['sectors'].reverse()
        with self.assertRaises(ValueError):published_top(bad)
        unfiltered=am_briefing.analyze(deepcopy(FIXTURE),generated_at=NOW)
        with self.assertRaisesRegex(ValueError,'product_filter'):published_top(unfiltered)
        for invalid in (0,6):
            with self.assertRaises(ValueError):published_top(self.report,invalid)
