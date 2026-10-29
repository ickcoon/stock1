"""Telegram tests: synthetic reports, fake HTTP transport, temporary state only."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests
from kiwoom_collector import am_momentum as am, telegram_briefing as tg

FIXTURE = json.loads((Path(__file__).parent/'fixtures/am_momentum_synthetic.json').read_text(encoding='utf-8'))
FAKE_TOKEN = '123456:'+'x'*30
FAKE_CHAT = '987654321'


def report():
    return am.analyze(deepcopy(FIXTURE),generated_at='2026-10-29T10:00:00+09:00')


def rehash(value):
    value['evaluation_reference']=tg.digest({k:v for k,v in value.items() if k!='evaluation_reference'})
    return value


class TelegramBriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name,value in [('PROJECT',self.root),('REPORTS',self.root/'reports'),('STATE',self.root/'notification_state')]:
            self.enterContext(patch.object(tg,name,value))
        tg.REPORTS.mkdir()
        self.report=report()
        self.json=tg.REPORTS/'analysis.json';self.html=tg.REPORTS/'analysis.html'
        self.save()
        self.config=tg.Configuration(FAKE_TOKEN,FAKE_CHAT,(FAKE_CHAT,))
        self.session=MagicMock()
        self.session.__enter__.return_value=self.session
        self.session.post.return_value=Mock(status_code=200,json=Mock(return_value=dict(ok=True,result=dict(message_id=1,chat=dict(id=int(FAKE_CHAT),type='private')))))
        self.factory=Mock(return_value=self.session)

    def save(self):
        self.json.write_text(json.dumps(self.report,ensure_ascii=False),encoding='utf-8')
        self.html.write_text(am.render(self.report),encoding='utf-8')

    def deliver(self,html=True):
        return tg.deliver(self.report,tg.summary(self.report,attached=html),am.render(self.report) if html else None,self.config,session_factory=self.factory)

    def test_parse_summary_preserves_cutoff_scope_and_provisional(self):
        parsed,text,html=tg.prepare(self.json,self.html)
        self.assertEqual(parsed,self.report)
        for fragment in ('2026-10-29','09:50:00 KST','mock','candidate_confirmed=false','TOP5 4회','005930','Semiconductor','시총 미검증/누락','다운로드'):
            self.assertIn(fragment,text)
        self.assertEqual(html,am.render(parsed))
        self.assertLess(len(text.encode('utf-16-le'))//2,4096)
        self.assertLess(text.index('1. 005930'),text.index('2. 000660'))

    def test_real_environment_is_still_synthetic_and_unconfirmed(self):
        fixture=deepcopy(FIXTURE)
        def real(v):
            if isinstance(v,dict):
                if 'environment' in v:v['environment']='real'
                for x in v.values():real(x)
            elif isinstance(v,list):
                for x in v:real(x)
        real(fixture)
        text=tg.summary(am.analyze(fixture,generated_at='2026-10-29T10:00:00+09:00'))
        self.assertIn('real / KRX / 합성 fixture',text)
        self.assertIn('candidate_confirmed=false',text)

    def test_missing_window_sends_insufficient_not_fake_top3(self):
        fixture=deepcopy(FIXTURE);fixture['rankings'].pop()
        value=am.analyze(fixture,generated_at='2026-10-29T10:00:00+09:00')
        text=tg.summary(value)
        self.assertIn('자료 부족',text)
        self.assertNotIn('1. 005930',text)
        self.assertNotIn('관심도 상위 3개:',text)

    def test_no_attention_candidates(self):
        fixture=deepcopy(FIXTURE);fixture['rankings']=[]
        value=am.analyze(fixture,generated_at='2026-10-29T10:00:00+09:00')
        self.assertIn('자료 부족',tg.summary(value))

    def test_dry_run_no_network_no_credentials_no_state(self):
        with patch.dict(os.environ,{},clear=True),patch.object(tg.requests,'Session',side_effect=AssertionError('network')),patch.object(tg,'deliver',side_effect=AssertionError('send')),patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(tg.main(['--report',str(self.json),'--html',str(self.html)]),0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['status'],'dry_run')
        self.assertTrue(result['html_validated'])
        self.assertFalse(tg.STATE.exists())

    def test_default_no_attachment(self):
        _,text,html=tg.prepare(self.json)
        self.assertIsNone(html)
        self.assertIn('미첨부',text)

    def test_send_methods_fields_and_transport_safety(self):
        result=self.deliver()
        self.assertEqual(result['status'],'sent')
        calls=self.session.post.call_args_list
        self.assertEqual(len(calls),2)
        self.assertTrue(calls[0].args[0].endswith('/sendMessage'))
        self.assertTrue(calls[1].args[0].endswith('/sendDocument'))
        self.assertEqual(calls[0].kwargs['json']['chat_id'],FAKE_CHAT)
        self.assertNotIn('parse_mode',calls[0].kwargs['json'])
        self.assertEqual(calls[1].kwargs['files']['document'][0],'arvis-am-review.html')
        self.assertEqual(calls[1].kwargs['files']['document'][1],am.render(self.report).encode('utf-8'))
        for call in calls:
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertTrue(call.kwargs['verify'])
            self.assertEqual(call.kwargs['timeout'],(5,20))
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.mount.call_args.args[1].max_retries.total,0)

    def test_message_only_send(self):
        self.assertEqual(self.deliver(html=False)['status'],'sent')
        self.assertEqual(self.session.post.call_count,1)

    def test_duplicate_same_date_type_schema_not_content_hash(self):
        self.deliver()
        self.report['generated_at']='2026-10-29T10:01:00+09:00';rehash(self.report)
        self.assertEqual(self.deliver()['status'],'duplicate_blocked')
        self.assertEqual(self.session.post.call_count,2)

    def test_dedup_environment_partition(self):
        self.deliver()
        value=deepcopy(self.report);value['environment']='real'
        for r in value['rows']:r['environment']='real'
        self.report=rehash(value)
        self.assertEqual(self.deliver()['status'],'sent')
        self.assertEqual(self.session.post.call_count,4)

    def test_http_failure_has_no_body_leak_and_no_retry(self):
        self.session.post.return_value=Mock(status_code=403,text=FAKE_TOKEN+' '+FAKE_CHAT)
        result=self.deliver()
        self.assertEqual(result['status'],'failed_or_unknown')
        self.assertEqual(result['reason'],'telegram_http_failed')
        self.assertEqual(self.session.post.call_count,1)
        self.assertEqual(self.deliver()['status'],'duplicate_blocked')
        self.assertNotIn(FAKE_TOKEN,json.dumps(result))

    def test_api_error_safe(self):
        self.session.post.return_value=Mock(status_code=200,json=Mock(return_value={'ok':False,'description':FAKE_TOKEN}))
        self.assertEqual(self.deliver()['reason'],'telegram_api_failed')
        self.assertEqual(self.session.post.call_count,1)

    def test_network_timeout_unknown_reservation_retained(self):
        self.session.post.side_effect=requests.Timeout('URL bot'+FAKE_TOKEN)
        result=self.deliver()
        self.assertEqual(result['reason'],'telegram_timeout')
        self.assertEqual(self.deliver()['status'],'duplicate_blocked')
        self.assertNotIn(FAKE_TOKEN,json.dumps(result))

    def test_connection_exception_types_are_public_and_no_request_details_escape(self):
        secret = FAKE_TOKEN+FAKE_CHAT
        cases = (
            (requests.ConnectionError(tg.socket.gaierror(-2,secret)),'telegram_dns_failed','gaierror'),
            (requests.ConnectionError(ConnectionRefusedError(secret)),'telegram_connection_failed','ConnectionError'),
            (requests.exceptions.SSLError(tg.ssl.SSLCertVerificationError(1,secret)),
             'telegram_certificate_failed','SSLCertVerificationError'),
            (requests.exceptions.SSLError(secret),'telegram_tls_failed','SSLError'),
            (requests.ConnectTimeout(secret),'telegram_connect_timeout','ConnectTimeout'),
            (requests.ReadTimeout(secret),'telegram_read_timeout','ReadTimeout'),
            (requests.ConnectionError(tg.ue.ReadTimeoutError(None,secret,secret)),
             'telegram_read_timeout','ReadTimeoutError'),
            (requests.ConnectionError(TimeoutError(secret)),'telegram_timeout','TimeoutError'),
        )
        for error, reason, label in cases:
            self.session.post.side_effect = error
            with self.assertRaises(tg.TelegramRequestError) as caught:
                tg.api_call(self.session,self.config,'sendMessage',text='synthetic')
            self.assertEqual(str(caught.exception),reason)
            self.assertEqual(caught.exception.diagnostics['exception_type'],label)
            self.assertEqual(caught.exception.diagnostics['stage'],'request_response')
            self.assertIsNone(caught.exception.diagnostics['http_status'])
            for value in (FAKE_TOKEN,FAKE_CHAT):
                self.assertNotIn(value,json.dumps(caught.exception.diagnostics))
        self.assertEqual(self.session.post.call_count,len(cases))

    @patch('kiwoom_collector.media_policy.require_legacy_media', new=lambda: None)
    def test_voice_upload_timeout_is_unknown_persisted_and_never_retried(self):
        from dataclasses import replace
        from kiwoom_collector import voice_briefing as vb
        script=vb.build_script(self.report)
        audio=replace(vb.SilenceFixtureTTS().synthesize(script),is_test=False)
        self.session.post.side_effect=requests.ConnectionError(TimeoutError(FAKE_TOKEN+FAKE_CHAT))
        result=tg.deliver(self.report,tg.summary(self.report),None,self.config,voice=(script,audio),
                          voice_only=True,session_factory=self.factory)
        self.assertEqual(result['status'],'failed_or_unknown')
        self.assertEqual(result['method'],'sendVoice')
        self.assertEqual(result['stage'],'upload_or_response')
        self.assertEqual(result['reason'],'telegram_timeout')
        self.assertFalse(result['voice_sent'])
        self.assertFalse(result['message_sent'])
        saved=json.loads(next(tg.STATE.iterdir()).read_text())
        self.assertEqual(saved['status'],'failed_or_unknown')
        self.assertEqual(saved['failure']['exception_type'],'TimeoutError')
        for secret in (FAKE_TOKEN,FAKE_CHAT):
            self.assertNotIn(secret,json.dumps(result)+json.dumps(saved))
        again=tg.deliver(self.report,tg.summary(self.report),None,self.config,voice=(script,audio),
                          voice_only=True,session_factory=self.factory)
        self.assertEqual(again['status'],'duplicate_blocked')
        self.assertEqual(self.session.post.call_count,1)

    def test_http_and_telegram_numeric_codes_ignore_description(self):
        for status, code in ((400,400),(401,401),(403,403),(429,429),(500,500),(200,400)):
            self.session.post.return_value=Mock(status_code=status,json=Mock(return_value=dict(
                ok=False,error_code=code,description=FAKE_TOKEN+FAKE_CHAT,
                parameters=dict(retry_after=FAKE_CHAT))))
            with self.assertRaises(tg.TelegramRequestError) as caught:
                tg.api_call(self.session,self.config,'sendMessage',text='synthetic')
            self.assertEqual(str(caught.exception),'telegram_api_failed' if status==200 else 'telegram_http_failed')
            self.assertEqual(caught.exception.diagnostics['http_status'],status)
            self.assertEqual(caught.exception.diagnostics['api_error_code'],code)
            self.assertNotIn(FAKE_TOKEN,json.dumps(caught.exception.diagnostics))
            self.assertNotIn(FAKE_CHAT,json.dumps(caught.exception.diagnostics))

    def test_invalid_json_and_wrong_recipient_are_response_stage_errors(self):
        self.session.post.return_value.json.side_effect=ValueError(FAKE_TOKEN)
        result=self.deliver(html=False)
        self.assertEqual(result['status'],'failed_or_unknown')
        self.assertEqual(result['reason'],'telegram_response_invalid')
        self.assertEqual(result['http_status'],200)
        self.assertEqual(result['stage'],'response_json')
        self.assertEqual(result['exception_type'],'JSONDecodeError')
        self.assertNotIn(FAKE_TOKEN,json.dumps(result))
        self.session.post.return_value=Mock(status_code=200,json=Mock(return_value=dict(
            ok=True,result=dict(message_id=1,chat=dict(id=1,type='private')))))
        with self.assertRaises(tg.TelegramRequestError) as caught:
            tg.api_call(self.session,self.config,'sendMessage',text='synthetic')
        self.assertEqual(caught.exception.diagnostics['stage'],'recipient_validation')

    @patch('kiwoom_collector.media_policy.require_legacy_media', new=lambda: None)
    def test_sendaudio_transport_uses_unchanged_tls_and_timeout_contract(self):
        from kiwoom_collector import voice_briefing as vb
        audio=vb.SilenceFixtureTTS().synthesize(vb.build_script(self.report)).data
        self.session.post.side_effect=requests.ConnectTimeout(FAKE_TOKEN)
        with self.assertRaises(tg.TelegramRequestError) as caught:
            tg.api_call(self.session,self.config,'sendAudio',audio=audio)
        self.assertEqual(caught.exception.diagnostics['method'],'sendAudio')
        self.assertEqual(caught.exception.diagnostics['stage'],'upload_or_response')
        kwargs=self.session.post.call_args.kwargs
        self.assertEqual(kwargs['timeout'],(5,20))
        self.assertTrue(kwargs['verify'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertIn('audio',kwargs['files'])
        self.assertEqual(self.session.post.call_count,1)

    def test_partial_document_failure_does_not_resend_message(self):
        ok=self.session.post.return_value
        self.session.post.side_effect=[ok,requests.ConnectionError(FAKE_TOKEN)]
        result=self.deliver()
        self.assertTrue(result['message_sent']);self.assertFalse(result['document_sent'])
        self.assertEqual(self.deliver()['status'],'duplicate_blocked')
        self.assertEqual(self.session.post.call_count,2)

    def test_invalid_response_and_wrong_recipient(self):
        for body in ({'ok':True},{'ok':True,'result':{'message_id':1,'chat':{'id':42,'type':'group'}}}):
            with self.subTest(body=body):
                self.session.post.return_value=Mock(status_code=200,json=Mock(return_value=body))
                with self.assertRaises(tg.BriefingError):tg.api_call(self.session,self.config,'sendMessage',text='safe')

    def test_allowlist_and_private_chat_validation(self):
        base={'ARVIS_TELEGRAM_BOT_TOKEN':FAKE_TOKEN,'ARVIS_TELEGRAM_CHAT_ID':FAKE_CHAT,'ARVIS_TELEGRAM_ALLOWED_CHAT_IDS':FAKE_CHAT}
        with patch.dict(os.environ,base,clear=True):self.assertEqual(tg.Configuration.from_environment().chat_id,FAKE_CHAT)
        for change in ({'ARVIS_TELEGRAM_ALLOWED_CHAT_IDS':''},{'ARVIS_TELEGRAM_ALLOWED_CHAT_IDS':'123'},{'ARVIS_TELEGRAM_CHAT_ID':'-123'},{'ARVIS_TELEGRAM_CHAT_ID':'@channel'},{'ARVIS_TELEGRAM_BOT_TOKEN':'bad'}):
            with self.subTest(change=change),patch.dict(os.environ,{**base,**change},clear=True):
                with self.assertRaises(tg.BriefingError):tg.Configuration.from_environment()
        self.assertNotIn(FAKE_TOKEN,repr(self.config))
        self.assertNotIn(FAKE_CHAT,repr(self.config))

    def test_send_requires_config_before_transport(self):
        with patch.dict(os.environ,{},clear=True),patch('sys.stdout',new_callable=io.StringIO) as output,patch.object(tg,'deliver',side_effect=AssertionError('network')):
            self.assertEqual(tg.main(['--report',str(self.json),'--send']),1)
        self.assertNotIn(str(self.json),output.getvalue())

    def test_plaintext_secrets_and_extra_fields_rejected(self):
        for key,value in [('api_key','secret'),('metadata',FAKE_TOKEN),('metadata',{'account_id':'111'}),('metadata',r'C:\Users\hospital')]:
            with self.subTest(key=key):
                document=deepcopy(self.report);document[key]=value;rehash(document)
                with self.assertRaises(ValueError):tg.validate(document)

    def test_configured_chat_and_token_cannot_appear_in_report(self):
        for value in (FAKE_TOKEN,FAKE_CHAT):
            document=deepcopy(self.report);document['rows'][0]['sectors'][0]['basis']=value;rehash(document)
            with self.assertRaises(ValueError):tg.validate(document,secrets=(FAKE_TOKEN,FAKE_CHAT))

    def test_state_contains_no_recipient_token_or_machine_path(self):
        self.deliver()
        state=''.join(p.name+p.read_text(encoding='utf-8') for p in tg.STATE.iterdir())
        for secret in (FAKE_CHAT,FAKE_TOKEN,str(self.root)):self.assertNotIn(secret,state)

    def test_active_html_or_mismatched_analysis_rejected(self):
        self.html.write_text(am.render(self.report)+'<script>fetch("x")</script>',encoding='utf-8')
        with self.assertRaises(tg.BriefingError):tg.prepare(self.json,self.html)
        other=deepcopy(self.report);other['generated_at']='2026-10-29T10:01:00+09:00';rehash(other)
        self.html.write_text(am.render(other),encoding='utf-8')
        with self.assertRaises(tg.BriefingError):tg.prepare(self.json,self.html)

    def test_fingerprint_and_confirmation_tampering_rejected(self):
        for field,value in [('evaluation_reference','x'),('candidate_confirmed',True),('source_kind','live_test')]:
            with self.subTest(field=field):
                doc=deepcopy(self.report);doc[field]=value
                with self.assertRaises(tg.BriefingError):tg.validate(doc)

    def test_future_times_and_false_counts_rejected_even_with_new_hash(self):
        for mutate in (lambda d:d['rows'][0]['rank_history'][-1].update(received_at='2026-10-29T09:51:00+09:00'),lambda d:d['rows'][0].update(top5_appearances=99),lambda d:d['completeness'].update(top20_complete=False)):
            doc=deepcopy(self.report);mutate(doc);rehash(doc)
            with self.assertRaises(tg.BriefingError):tg.validate(doc)

    def test_report_path_size_json_and_duplicate_keys(self):
        outside=self.root/'outside.json';outside.write_text('{}',encoding='utf-8')
        with self.assertRaises(tg.BriefingError):tg.prepare(outside)
        self.json.write_text('{"schema_version":1,"schema_version":2}',encoding='utf-8')
        with self.assertRaises(tg.BriefingError):tg.prepare(self.json)
        self.json.write_text('x'*20,encoding='utf-8')
        with patch.object(tg,'MAX_JSON',10):
            with self.assertRaises(tg.BriefingError):tg.prepare(self.json)
        self.json.write_text('{',encoding='utf-8')
        with patch('sys.stdout',new_callable=io.StringIO) as output:self.assertEqual(tg.main(['--report',str(self.json)]),1)
        self.assertNotIn(str(self.root),output.getvalue())

    def test_summary_or_document_substitution_before_send_blocked(self):
        for text,html in [('fake summary',None),(tg.summary(self.report,attached=True),'<html>fake</html>')]:
            with self.assertRaises(tg.BriefingError):tg.deliver(self.report,text,html,self.config,session_factory=self.factory)
        self.factory.assert_not_called()

    def test_transport_factory_exception_is_static_and_reserved(self):
        self.factory.side_effect=tg.BriefingError(FAKE_TOKEN)
        result=self.deliver()
        self.assertEqual(result['reason'],'delivery_failed')
        self.assertNotIn(FAKE_TOKEN,json.dumps(result))
        self.assertEqual(self.deliver()['status'],'duplicate_blocked')

    def test_input_immutable(self):
        before=deepcopy(self.report)
        tg.summary(self.report);self.deliver()
        self.assertEqual(before,self.report)

    def test_no_operating_db_or_d0_calls(self):
        from kiwoom_collector import d0_store
        with patch('sqlite3.connect',side_effect=AssertionError('DB')),patch.object(d0_store,'save_record',side_effect=AssertionError('D0')),patch('socket.socket.connect',side_effect=AssertionError('network')):
            tg.prepare(self.json,self.html)
            self.assertEqual(self.deliver()['status'],'sent')

    def test_concurrent_send_reservation_allows_only_one_attempt(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as workers:
            results=list(workers.map(lambda _:self.deliver(),range(8)))
        self.assertEqual(sum(r['status']=='sent' for r in results),1)
        self.assertEqual(sum(r['status']=='duplicate_blocked' for r in results),7)
        self.assertEqual(self.session.post.call_count,2)
        self.assertEqual(len(list(tg.STATE.iterdir())),1)

    def test_preflight_text_only_no_network_or_state_writes(self):
        with patch.object(tg.Configuration,'from_environment',return_value=self.config), patch.object(tg.requests,'Session') as session:
            result=tg.preflight(self.json)
        self.assertEqual(result['status'],'preflight_ok')
        self.assertFalse(result['html_validated'])
        self.assertEqual(result['environment'],'mock')
        self.assertFalse(result['candidate_confirmed'])
        session.assert_not_called()
        self.assertFalse(tg.STATE.exists())
        for secret in (FAKE_TOKEN,FAKE_CHAT,str(self.root)):
            self.assertNotIn(secret,json.dumps(result))

    def test_preflight_configuration_reports_only_static_failure(self):
        for code in ('configuration_required','private_chat_allowlist_required','recipient_not_allowed',FAKE_TOKEN):
            with patch.object(tg.Configuration,'from_environment',side_effect=tg.BriefingError(code)):
                result=tg.preflight(self.json)
            self.assertEqual(result['stage'],'configuration')
            self.assertEqual(result['reason'],code if code != FAKE_TOKEN else 'validation_failed')
            self.assertNotIn(FAKE_TOKEN,json.dumps(result))

    def test_preflight_json_and_optional_html_are_separate_stages(self):
        with patch.object(tg.Configuration,'from_environment',return_value=self.config):
            self.html.write_text('invalid attachment',encoding='utf-8')
            self.assertEqual(tg.preflight(self.json)['status'],'preflight_ok')
            result=tg.preflight(self.json,self.html)
            self.assertEqual(result['stage'],'html_attachment')
            self.assertEqual(result['reason'],'html_analysis_mismatch')
            self.json.write_text('{}',encoding='utf-8')
            self.assertEqual(tg.preflight(self.json)['stage'],'json_report')

    def test_preflight_detects_secret_in_report(self):
        config=tg.Configuration(FAKE_TOKEN,'5930',('5930',))
        with patch.object(tg.Configuration,'from_environment',return_value=config):
            result=tg.preflight(self.json)
        # Privacy checks run before any transport; no bypass for fixture content.
        self.assertEqual(result['reason'],'sensitive_content_rejected')
        self.assertFalse(tg.STATE.exists())

    def test_check_cli_and_existing_reservation_are_read_only(self):
        self.deliver(html=False)
        before={p.name:p.read_bytes() for p in tg.STATE.iterdir()}
        with patch.object(tg.Configuration,'from_environment',return_value=self.config), patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(tg.main(['--report',str(self.json),'--check']),1)
        self.assertEqual(json.loads(output.getvalue())['status'],'duplicate_blocked')
        self.assertEqual(before,{p.name:p.read_bytes() for p in tg.STATE.iterdir()})
