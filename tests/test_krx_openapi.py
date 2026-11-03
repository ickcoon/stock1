import copy
import json
from pathlib import Path
import unittest
import contextlib
import io
import tempfile
from unittest.mock import patch
import requests

from kiwoom_collector.krx_openapi import SERVICES,Credential,KrxError,ProbeClient,parse,preflight,main
from kiwoom_collector import krx_openapi as krx

FIXTURE=Path(__file__).parent/'fixtures'/'krx_an_synthetic.json'

class KrxTests(unittest.TestCase):
    def setUp(self):self.doc=json.loads(FIXTURE.read_text(encoding='utf8'))
    def parsed(self,service,body=None,masters=()):
        return parse(body or self.doc['responses'][service],service=service,bas_dd='20261102',
                     source_kind='synthetic_fixture',base_records=masters)
    def test_seven_services_no_real_data(self):
        self.assertEqual(len(SERVICES),7)
        for service in SERVICES:
            r=self.parsed(service)
            self.assertEqual(r['row_count'],1)
            self.assertTrue(r['synthetic'])
            self.assertFalse(r['units_runtime_verified'])
            self.assertEqual(r['daily_database_rows_written'],0)
    def test_code_namespaces_and_zero(self):
        master=self.parsed('ksq_isu_base_info')['records']
        self.assertEqual(master[0]['stock_code'],'0000A0')
        row=self.parsed('ksq_bydd_trd',masters=master)['records'][0]
        self.assertEqual(row['stock_code'],'0000A0')
        self.assertTrue(row['identity_master_join_matches'])
        self.assertFalse(row['identity_verified'])
        self.assertIsNone(self.parsed('ksq_bydd_trd')['records'][0]['stock_code'])
    def test_wrong_date_and_duplicates_rejected(self):
        for service in ('stk_bydd_trd','kospi_dd_trd'):
            body=copy.deepcopy(self.doc['responses'][service])
            body['OutBlock_1'][0]['BAS_DD']='20261101'
            with self.assertRaises(KrxError):self.parsed(service,body)
            body=copy.deepcopy(self.doc['responses'][service])
            body['OutBlock_1']*=2
            with self.assertRaises(KrxError):self.parsed(service,body)
    def test_indices_never_supply_breadth(self):
        for service in ('krx_dd_trd','kospi_dd_trd','kosdaq_dd_trd'):
            row=self.parsed(service)['records'][0]
            self.assertFalse(row['adr_breadth_source'])
            self.assertNotIn('stock_code',row)
    def test_empty_unknown_not_holiday(self):
        r=self.parsed('kospi_dd_trd',{'OutBlock_1':[]})
        self.assertEqual(r['status'],'empty_response_unclassified')
    def test_invalid_ohlc_and_numeric_rejected(self):
        for key,value in [('HGPRC_IDX','90'),('ACC_TRDVAL','1,00')]:
            body=copy.deepcopy(self.doc['responses']['kospi_dd_trd'])
            body['OutBlock_1'][0][key]=value
            with self.assertRaises(KrxError):self.parsed('kospi_dd_trd',body)
    def test_approval_blocks_transport(self):
        class Session:
            def get(self,*a,**k):raise AssertionError('network must not run')
        for service in SERVICES:
            with self.assertRaisesRegex(KrxError,'approval_unverified'):
                ProbeClient(Credential('fixture-secret'),{},session=Session()).probe(service,'20261102')
        with patch.dict('os.environ',{},clear=True):
            r=preflight()
        self.assertFalse(r['key_present'])
        self.assertEqual(len(r['services']),7)
    def test_header_route_single_attempt_and_redaction(self):
        outer=self
        class Response:
            status_code=200
            content=b'{}'
            def json(self):return outer.doc['responses']['kospi_dd_trd']
        class Session:
            def get(self,url,**kwargs):
                outer.assertTrue(url.endswith('/idx/kospi_dd_trd'))
                outer.assertEqual(kwargs['headers'],{'AUTH_KEY':'fixture-secret'})
                outer.assertNotIn('fixture-secret',url)
                outer.assertFalse(kwargs['allow_redirects'])
                outer.assertTrue(kwargs['verify'])
                return Response()
        review={'synthetic':True,'services':{'kospi_dd_trd':dict(approved=True,contract_verified=True,
            method='GET',official_spec_evidence='synthetic only',approval_evidence='synthetic only')}}
        client=ProbeClient(Credential('fixture-secret'),review,session=Session(),synthetic_transport=True)
        self.assertEqual(client.probe('kospi_dd_trd','20261102')['actual_api_calls'],0)
        with self.assertRaisesRegex(KrxError,'already_attempted'):client.probe('kospi_dd_trd','20261102')
        self.assertNotIn('fixture-secret',repr(client))
        with self.assertRaisesRegex(KrxError,'fixture_approval'):
            ProbeClient(Credential('fixture-secret'),review,session=Session()).probe('kospi_dd_trd','20261102')

    def transport(self,status,body,*,error=None):
        class Response:
            status_code=status
            content=b'{}'
            def json(self):return body
        class Session:
            def get(self,*args,**kwargs):
                if error:raise error
                return Response()
        review={'synthetic':True,'services':{'stk_isu_base_info':dict(read_only_probe_authorized=True,
            contract_verified=True,method='GET',official_spec_evidence='synthetic request contract')}}
        return ProbeClient(Credential('fixture-secret'),review,session=Session(),synthetic_transport=True)

    def test_http_errors_and_unknown_portal_approval(self):
        for http,status in ((401,'authorization_denied'),(403,'authorization_denied'),(429,'quota_limited'),(500,'http_error')):
            client=self.transport(http,{'resp_code':'E1','resp_msg':'fixture-secret','AUTH_KEY':'private-other'})
            result=client.probe('stk_isu_base_info','20261102')
            self.assertEqual(result['http_status'],http)
            self.assertEqual(result['status'],status)
            self.assertEqual(result['portal_approval'],'unverified')
            self.assertNotIn('fixture-secret',json.dumps(result))
            self.assertNotIn('private-other',json.dumps(result))
            self.assertEqual(client.transport_calls,1)

    def test_200_error_missing_fields_and_empty_are_not_success(self):
        for body,status in (({'resp_code':'401','resp_msg':'invalid key'},'krx_error_response'),
                            ({'OutBlock_1':[{'ISU_CD':'000001'}]},'response_contract_mismatch'),
                            ({'OutBlock_1':[]},'empty_response_unclassified')):
            result=self.transport(200,body).probe('stk_isu_base_info','20261102')
            self.assertEqual(result['status'],status)
            self.assertEqual(result['normal_data_rows'],0)
            self.assertEqual(result['authentication'],'unverified')

    def test_tls_failure_is_not_retried_or_silenced(self):
        client=self.transport(200,{},error=requests.exceptions.SSLError('fixture-secret'))
        result=client.probe('stk_isu_base_info','20261102')
        self.assertEqual(result['status'],'tls_verification_failed')
        self.assertEqual(client.transport_calls,1)
        self.assertIsNone(result['http_status'])
        self.assertNotIn('fixture-secret',json.dumps(result))

    def test_missing_key_exits_normally_without_writes(self):
        with patch.dict('os.environ',{},clear=True),patch.object(Path,'mkdir') as mkdir,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--execute']),0)
        self.assertEqual(json.loads(output.getvalue())['status'],'not_executed_key_missing')
        mkdir.assert_not_called()

    def test_missing_local_key_file_exits_normally(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(krx,'PROJECT',Path(folder).resolve()),contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--execute','--env-file',str(Path(folder)/'.env.krx.local')]),0)
            self.assertEqual(json.loads(output.getvalue())['status'],'not_executed_key_missing')

    def test_common_preferred_remain_distinct(self):
        body=copy.deepcopy(self.doc['responses']['stk_isu_base_info'])
        other=copy.deepcopy(body['OutBlock_1'][0])
        other.update(ISU_CD='KR7SYNTH0003',ISU_SRT_CD='000005',ISU_NM='SYNTHETIC PREFERRED',
                     ISU_ABBRV='SYNTHETIC P',KIND_STKCERT_TP_NM='PREFERRED')
        body['OutBlock_1'].append(other)
        rows=self.parsed('stk_isu_base_info',body)['records']
        self.assertEqual([r['stock_code'] for r in rows],['000001','000005'])
        self.assertNotEqual(rows[0]['stock_type_raw'],rows[1]['stock_type_raw'])

    def test_duplicate_standard_code_not_hidden_by_different_short_code(self):
        body=copy.deepcopy(self.doc['responses']['stk_isu_base_info'])
        other=copy.deepcopy(body['OutBlock_1'][0]);other['ISU_SRT_CD']='000005'
        body['OutBlock_1'].append(other)
        with self.assertRaisesRegex(KrxError,'duplicate_standard_code'):self.parsed('stk_isu_base_info',body)

    def test_fixture_and_preflight_never_connect_sqlite(self):
        with patch('sqlite3.connect',side_effect=AssertionError('database write forbidden')),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--fixture',str(FIXTURE)]),0)
        result=json.loads(output.getvalue())
        self.assertEqual(len(result['results']),7)
        self.assertEqual(result['actual_api_calls'],0)

    def test_receipts_prevent_second_process_recall(self):
        class Client:
            actual_api_calls=0
            calls=0
            def __init__(self,*a,**k):pass
            def close(self):pass
            def probe(self,service,date,**kwargs):
                Client.calls+=1
                return dict(service=service,source_kind='krx_live',basis_date='2026-11-02',status='fixture_receipt_test',records=[])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            with patch.object(krx,'OUTPUT_ROOT',root),patch.object(krx,'ProbeClient',Client),patch.object(krx,'credential',return_value=Credential('fixture-secret')),contextlib.redirect_stdout(io.StringIO()):
                args=['--execute','--bas-dd','20261102','--output-dir',str(root/'receipts')]
                main(args);main(args)
            self.assertEqual(Client.calls,7)
            self.assertEqual(len(list((root/'receipts').glob('*.json'))),7)
            with patch.object(krx,'credential',return_value=Credential('fixture-secret')),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(KrxError,'development_output_required'):
                    main(['--execute','--bas-dd','20261102','--output-dir',str(root/'operational')])

if __name__=='__main__':unittest.main()
