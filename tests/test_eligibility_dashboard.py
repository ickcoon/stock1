"""Step 6-D offline evidence, boundaries and desktop/mobile interactions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from bubble_browser_helpers import browser_page
from test_bubble_cross import source
from kiwoom_collector import eligibility_dashboard as ui, cross_adapter as ca, capital_filter as cf, market_cap as mc

FIXTURE = json.loads((Path(__file__).parent/'fixtures/eligibility_dashboard_synthetic.json').read_text(encoding='utf-8'))


def scenario(case=None):
    case = case or {'mac':'3000'}
    doc = source()
    doc['source_kind']='fixture'
    at = FIXTURE['at']
    doc['ranking'].update(collected_at=at, test_mode=False)
    doc['turnover'].update(collected_at=at, request_started_at='2026-10-27T15:29:59+09:00',
        response_received_at=at, status='success', pagination_complete=True, test_mode=False)
    kind = case.get('kind','common_stock')
    doc['code_registry']['005930'] = dict(verified=True,source='SYNTHETIC',security_type=kind)
    doc['turnover']['rows'][0]['trde_prica'] = case.get('turnover','100000')
    caps = []
    proof = {}
    if case.get('mac') is not None:
        cap = mc.parse_response(dict(return_code=0,stk_cd='005930',stk_nm='합성 삼성전자',mac=case['mac'],flo_stk='10000',cur_prc='+30000'),
            requested_code='005930',environment='mock',request_started_at='2026-10-27T15:29:59+09:00',
            response_received_at=at,security_type=kind,security_type_evidence='SYNTHETIC type evidence')
        report = ca.analyze_pair(doc,min_turnover_eok=1000)
        capital_proof = dict(for_record=cap['fingerprint'],environment='mock',source='SYNTHETIC evidence',
            mac_unit_runtime_verified=True,market_cap_basis_verified=True,market_asof=at,trade_date='2026-10-27')
        proof['005930'] = dict(cross_context=cf.context_key(report,report['rows'][0]),capital=capital_proof,
            turnover=dict(market_asof=at,trade_date='2026-10-27',unit_runtime_verified=True,
                basis=case.get('basis','closing_cumulative'),closing_basis_verified=True,
                market_conditions_verified=True,evidence='SYNTHETIC closing proof',sector_co_rise=True,sector_co_rise_verified=True))
        if case.get('wrong_code'):
            cap['raw']['stk_cd']='000660'
        if case.get('mixed'):
            cap['environment']='real'
        if case.get('future'):
            cap.update(request_started_at='2026-10-27T16:46:00+09:00',response_received_at='2026-10-27T16:46:01+09:00')
        caps.append(cap)
    return doc,caps,proof


def build(case=None):
    doc,caps,proof = scenario(case)
    return ui.build(doc,capital_records=caps,capital_proofs=proof)


class EligibilityDashboardTests(unittest.TestCase):
    def test_required_synthetic_cases(self):
        for case in FIXTURE['cases']:
            with self.subTest(case=case['name']):
                if case.get('mixed'):
                    with self.assertRaises(ValueError):build(case)
                    continue
                bundle=build(case);row=bundle['eligibility']['rows'][0]
                self.assertEqual(row['qualification']['review_status'],case['status'])
                if case.get('band'):
                    self.assertEqual(row['capital_band']['band'],case['band'])
                self.assertFalse(row['candidate_confirmed'])
                self.assertFalse(row['qualification']['sector_detection_excluded'])
                if case.get('future') or case.get('wrong_code'):
                    self.assertIsNone(row['capital_reference'])
                if case.get('kind'):
                    self.assertTrue(row['separate_type_review'])

    def test_future_cap_absent_in_selected_and_earlier_html(self):
        bundle=build(dict(mac='98765',future=True))
        for frame in bundle['timeline']['frames']:
            html=ui.frame_html(frame,bundle)
            self.assertNotIn('98765',html)
            self.assertNotIn('16:46',html)

    def test_missing_cap_not_filled_from_other_stock(self):
        bundle=build()
        hynix=next(r for r in bundle['eligibility']['rows'] if r['stock_code']=='000660')
        self.assertIsNone(hynix['capital_reference'])

    def test_closing_cash_check_is_independent_of_missing_cap(self):
        doc,caps,proof=scenario()
        row=ui.build(doc,capital_records=[],capital_proofs=proof)['eligibility']['rows'][0]
        self.assertIsNone(row['qualification']['checks']['market_cap_3000_eok'])
        self.assertTrue(row['qualification']['checks']['closing_turnover_1000_eok'])
        self.assertEqual(row['qualification']['review_status'],'검증 보류')

    def test_cap_venue_mismatch_and_unknown_observation(self):
        for mode in ('venue','timestamp'):
            doc,caps,proof=scenario()
            if mode=='venue':caps[0]['venue']='NXT'
            else:caps[0]['request_started_at']=None
            row=ui.build(doc,capital_records=caps,capital_proofs=proof)['eligibility']['rows'][0]
            self.assertEqual(row['qualification']['review_status'],'검증 보류')

    def test_market_time_cannot_follow_actual_cash_response(self):
        doc,caps,proof=scenario()
        doc['turnover']['response_received_at']='2026-10-27T15:29:59.500000+09:00'
        row=ui.build(doc,capital_records=caps,capital_proofs=proof)['eligibility']['rows'][0]
        self.assertFalse(row['qualification']['final_review_allowed'])
        self.assertIn('turnover_market_time_after_response',row['qualification']['reasons'])

    def test_date_market_and_future_turnover_hold(self):
        for mode in ('date','market','unknown_market','future_cash','missing_end','partial'):
            with self.subTest(mode=mode):
                doc,caps,proof=scenario()
                if mode=='date':caps[0].update(request_started_at='2026-10-26T15:29:59+09:00',response_received_at='2026-10-26T15:30:00+09:00')
                if mode=='market':doc['turnover']['market']['stex_tp']='2'
                if mode=='unknown_market':doc['ranking']['market']=None
                if mode=='future_cash':doc['turnover']['response_received_at']='2026-10-27T16:46:00+09:00'
                if mode=='missing_end':doc['turnover']['response_received_at']=None
                if mode=='partial':doc['turnover'].update(status='partial',pagination_complete=False)
                bundle=ui.build(doc,capital_records=caps,capital_proofs=proof)
                row=bundle['eligibility']['rows'][0]
                self.assertEqual(row['qualification']['review_status'],'검증 보류')
                self.assertFalse(row['qualification']['final_review_allowed'])
                if mode in ('future_cash','missing_end','market'):self.assertFalse(row['turnover_visible'])

    def test_no_financial_filter_removes_top5_or_top20(self):
        bundle=build(dict(mac='2999',turnover='1',basis='intraday_provisional'))
        frame=bundle['timeline']['frames'][-1]
        html=ui.frame_html(frame,bundle)
        self.assertIn('data-code="005930"',html)
        self.assertIn('data-code="000660"',html)
        self.assertIn('TOP20 보조 관찰',html)
        self.assertIn('장중 미달도 관찰 유지',html)

    def test_immutable_inputs_geometry_and_timeline(self):
        doc,caps,proof=scenario();before=deepcopy((doc,caps,proof))
        bundle=ui.build(doc,capital_records=caps,capital_proofs=proof)
        baseline=ui.bubble.build(doc)
        self.assertEqual((doc,caps,proof),before)
        for actual, original in zip(bundle['timeline']['frames'],baseline['timeline']['frames']):
            self.assertEqual(actual['model'],original['model'])
            self.assertEqual(actual['focus'],original['focus'])
            self.assertEqual(actual['collected_at'],original['collected_at'])
        for frame in bundle['timeline']['frames']:
            original=deepcopy(frame);ui.frame_html(frame,bundle);self.assertEqual(frame,original)

    def test_hostile_values_escaped_and_raw_conversions_preserved(self):
        doc,caps,proof=scenario();doc['ranking']['rows'][0]['stk_nm']='<script>bad()</script>'
        bundle=ui.build(doc,capital_records=caps,capital_proofs=proof)
        html=ui.frame_html(bundle['timeline']['frames'][-1],bundle)
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('ka10001 원본 mac',html)
        self.assertIn('3,000.00억원',html)
        self.assertIn('장 마감 검증값',html)
        self.assertIn('candidate_confirmed=false',html)
        self.assertEqual(caps[0]['raw']['cur_prc'],'+30000')

    def test_proof_binding_and_duplicate_caps_cannot_pass(self):
        for mode in ('tampered_proof','duplicate','type'):
            doc,caps,proof=scenario()
            if mode=='tampered_proof':proof['005930']['cross_context']='wrong'
            if mode=='duplicate':caps.append(deepcopy(caps[0]))
            if mode=='type':doc['code_registry']['005930']['security_type']='unknown'
            bundle=ui.build(doc,capital_records=caps,capital_proofs=proof)
            self.assertEqual(bundle['eligibility']['rows'][0]['qualification']['review_status'],'검증 보류')

    def test_cli_saved_json_new_output_and_no_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);doc,caps,proof=scenario()
            for name,value in [('input',doc),('caps',caps),('proofs',proof)]:
                (path/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
            args=['--input-json',str(path/'input.json'),'--capital-json',str(path/'caps.json'),
                  '--proofs-json',str(path/'proofs.json'),'--output',str(path/'out.html')]
            self.assertEqual(ui.main(args),0)
            with self.assertRaises(SystemExit):ui.main(args)
            with self.assertRaises(SystemExit):ui.main(['--input-json','operating.db','--output',str(path/'other.html')])

    def test_desktop_mobile_sector_details_timeline_and_overflow(self):
        bundle=build();html=ui.render(bundle)
        for width,height in ((1360,900),(390,844)):
            with self.subTest(width=width),tempfile.TemporaryDirectory() as folder:
                with browser_page(html,folder,width=width,height=height) as (command,ev,click):
                    def inner(js):return ev('(()=>{const d=document.querySelector("#frame-1").contentDocument;'+js+'})()')
                    self.assertTrue(inner('return d.documentElement.scrollWidth<=d.documentElement.clientWidth+1;'))
                    self.assertIn('검토 가능',inner('return d.querySelector(".eligibility-card[data-code=\\"005930\\"]").textContent;'))
                    ev('document.querySelector("#frame-1").contentDocument.querySelector(".eligibility-card .eligibility-details").open=true')
                    self.assertTrue(inner('return d.querySelector(".eligibility-details").open;'))
                    inner('d.querySelector("#pick-unclassified").click(); return true;')
                    self.assertNotEqual(inner('return d.defaultView.getComputedStyle(d.querySelector("#eligibility-unclassified")).display;'),'none')
                    ev('document.querySelector("#time-0").click()')
                    old=ev('document.querySelector("#frame-0").contentDocument.querySelector(".cross-section").textContent')
                    self.assertNotIn('3,000.00억원',old)
                    self.assertIn('미래 관측',old)


if __name__=='__main__':unittest.main()
