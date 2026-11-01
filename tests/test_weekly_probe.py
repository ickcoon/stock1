"""C14-E recovery: explicit synthetic replies; no actual API, OAuth or DB."""
from copy import deepcopy
from dataclasses import asdict,replace
from datetime import datetime,timedelta
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from kiwoom_collector.weekly_probe_cli import main,WeeklyHTTPTransport
from kiwoom_collector.daily_supply_cli import HTTPTransport,identity
from kiwoom_collector import weekly_chart as w
from kiwoom_collector.config import Credentials
from kiwoom_collector.client import KST
from test_weekly_hybrid import weekly_fixture


class WeeklyProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.doc,self.page,self.review_args=weekly_fixture()
        self.daily=self.write('daily.json',self.doc)
        self.args=['--stock-code','005930','--environment','mock','--base-date','20261027','--adjustment','1']
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def write(self,name,value):
        path=self.root/name;path.write_text(json.dumps(value),encoding='utf-8');return path

    def fixture(self,http_status=200):
        reply=dict(body=self.page.raw,http_status=http_status,headers={'api-id':'ka10082','cont-yn':'N'},
            request_started_at=self.page.request_started_at.isoformat(),response_received_at=self.page.response_received_at.isoformat())
        return self.write('replay.json',dict(schema_version='daily-supply-replay-v1',
            responses=[dict(request=identity(self.page.request),continuation={},reply=reply)]))

    def review(self):
        page=w.parse_response(self.page.raw,request=self.page.request,response_environment='mock',
            request_started_at=self.page.request_started_at,response_received_at=self.page.response_received_at,
            source='weekly documented-field projection',cont_yn='N',response_api_id='ka10082')
        return self.write('review.json',dict(scope={**identity(self.page.request),'data_origin':'synthetic_fixture'},
            page_fingerprints=[page.fingerprint],price_basis=self.review_args['weekly_price_basis'],
            calendar=self.review_args['weekly_calendar'],evidence=[asdict(replace(e,page_fingerprint=page.fingerprint))
                for e in self.review_args['evidence']]))

    def execute(self,extra,folder):
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            code=main(self.args+['--daily-input',str(self.daily),'--output-dir',str(folder)]+extra)
        return code,json.loads(output.getvalue())

    def test_default_plan_never_reads_files_credentials_or_constructs_http(self):
        with patch('kiwoom_collector.weekly_probe_cli.read_json',side_effect=AssertionError('file read')),\
             patch('kiwoom_collector.weekly_probe_cli.credentials',side_effect=AssertionError('credentials')),\
             patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(self.args),0)
        result=json.loads(out.getvalue());self.assertEqual(result['api_calls'],0)
        self.assertEqual(result['max_weekly_requests'],2)

    def test_replay_review_outputs_weekly_and_unchanged_daily_without_live_claim(self):
        folder=self.root/'output';code,summary=self.execute(['--replay-json',str(self.fixture()),
            '--weekly-review-json',str(self.review())],folder)
        self.assertEqual(code,0);self.assertEqual(summary['verified_completed_weeks'],52)
        self.assertFalse(summary['live_requests_attempted']);self.assertFalse(summary['api_response_reviewed'])
        self.assertEqual(summary['collection']['api_calls'],0)
        result=json.loads((folder/'hybrid.json').read_text(encoding='utf-8'))
        self.assertEqual(result['primary_high52']['prior_high'],'153')
        self.assertEqual(result['daily']['moving_averages']['MA20']['value'],'342.5')
        self.assertNotIn('999999',json.dumps(result))
        self.assertFalse(result['order_signal'])

    def test_no_review_keeps_unavailable_and_saved_capture_reanalysis_needs_no_http(self):
        folder=self.root/'capture';code,summary=self.execute(['--replay-json',str(self.fixture())],folder)
        self.assertEqual(code,0);self.assertEqual(summary['high52_status'],'unavailable')
        with patch('kiwoom_collector.weekly_probe_cli.ReplayTransport',side_effect=AssertionError('no transport')):
            code,summary=self.execute(['--saved-weekly-json',str(folder/'weekly-capture.json'),
                '--weekly-review-json',str(self.review())],self.root/'reviewed')
        self.assertEqual(code,0);self.assertEqual(summary['high52_status'],'available')
        self.assertEqual(summary['collection']['attempts'],0)

    def test_api_failure_is_unavailable_and_does_not_retry_or_echo_server_secret(self):
        path=self.fixture(401);fixture=json.loads(path.read_text())
        fixture['responses'][0]['reply']['body'].update(token='SYNTHETIC_SECRET_TOKEN',return_msg='SYNTHETIC_SECRET_TOKEN')
        path.write_text(json.dumps(fixture))
        folder=self.root/'failed';code,summary=self.execute(['--replay-json',str(path),'--weekly-review-json',str(self.review())],folder)
        self.assertEqual(code,2);self.assertEqual(summary['collection']['attempts'],1)
        self.assertEqual(summary['high52_status'],'unavailable')
        self.assertNotIn('SYNTHETIC_SECRET_TOKEN',''.join(p.read_text(encoding='utf-8') for p in folder.glob('*.json')))

    def test_wrong_scope_existing_output_and_missing_live_approval_are_blocked(self):
        for extra in (['--live'],['--replay-json',str(self.fixture()),'--output-dir',str(self.root)]):
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
                self.execute(extra,self.root/'unused')
        wrong=deepcopy(self.doc);wrong['stock_code']='000660'
        self.daily=self.write('wrong.json',wrong)
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
            self.execute(['--replay-json',str(self.fixture())],self.root/'mismatch')
        self.assertFalse((self.root/'mismatch').exists())

    def test_capture_tampering_and_review_binding_mismatch_fail_closed(self):
        folder=self.root/'capture';self.execute(['--replay-json',str(self.fixture())],folder)
        path=folder/'weekly-capture.json';saved=json.loads(path.read_text())
        saved['pages'][0]['body'][w.LIST_KEY][5]['high_pric']='99999';path.write_text(json.dumps(saved))
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
            self.execute(['--saved-weekly-json',str(path)],self.root/'bad')
        review=self.review();value=json.loads(review.read_text());value['page_fingerprints']=['wrong'];review.write_text(json.dumps(value))
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
            self.execute(['--replay-json',str(self.fixture()),'--weekly-review-json',str(review)],self.root/'bad-review')

    def test_weekly_live_transport_uses_isolated_c13_without_oauth_and_daily_stays_daily_only(self):
        creds=Credentials('SYNTHETIC_APP','SYNTHETIC_SECRET','mock')
        expiry=(datetime.now(KST)+timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):HTTPTransport(creds,[self.page.request],'TOKEN',approved=True)
        transport=WeeklyHTTPTransport(creds,self.page.request,'SYNTHETIC_TOKEN',expiry,approved=True)
        self.addCleanup(transport.close)
        self.assertIsNone(transport.manager.cache)
        self.assertTrue(transport.client.session.verify);self.assertFalse(transport.client.session.trust_env)
        response=Mock(status_code=200,headers={'api-id':'ka10082','cont-yn':'N'});response.json.return_value=self.page.raw
        def fake(path,body,headers,*,before_send):
            self.assertEqual(path,'/api/dostk/chart');self.assertEqual(headers['api-id'],'ka10082')
            before_send();return response
        with patch.object(transport.client,'_request',side_effect=fake),\
             patch.object(transport.client,'authenticate',side_effect=AssertionError('no OAuth')):
            transport.fetch(self.page.request,{},time.perf_counter()+2)
        self.assertEqual(transport.calls,1)
        with self.assertRaises(ValueError):WeeklyHTTPTransport(creds,self.page.request,'TOKEN','2000-01-01T00:00:00+09:00',approved=True)
        with self.assertRaises(ValueError):WeeklyHTTPTransport(creds,self.page.request,'TOKEN',expiry,approved=False)

    def test_live_missing_token_metadata_is_blocked_before_credentials_or_http(self):
        doc=deepcopy(self.doc);doc['data_origin']='api_response';self.daily=self.write('api-daily.json',doc)
        with patch.dict('os.environ',{},clear=True),\
             patch('kiwoom_collector.weekly_probe_cli.credentials',side_effect=AssertionError('no credentials read')),\
             patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
            self.execute(['--live','--execute-approved','--quota-reviewed','--credentials-env-file','not-opened.env'],self.root/'live')


if __name__=='__main__':unittest.main()
