"""No live APIs: bounded fake Speech API and Telegram transports only."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from kiwoom_collector import openai_tts as ot, telegram_briefing as tg, voice_briefing as vb
from test_voice_foundation import report, FAKE_CHAT, FAKE_TOKEN

FAKE_KEY = 'sk-proj-'+('q'*32)
MP3 = (b'\xff\xfb\x90\x00'+bytes(413))*2500


class OpenAITTSTests(unittest.TestCase):
    def setUp(self):
        # Retired adapter diagnostics are tested only with fake transports.
        self.enterContext(patch('kiwoom_collector.media_policy.require_legacy_media'))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for key, value in [('PROJECT', self.root), ('REPORTS', self.root/'reports'), ('STATE', self.root/'notification_state')]:
            self.enterContext(patch.object(tg, key, value))
        self.enterContext(patch.dict('os.environ', {}, clear=True))
        tg.REPORTS.mkdir()
        self.report = report()
        self.script = vb.build_script(self.report)
        self.report_path = tg.REPORTS/'report.json'
        self.report_path.write_text(json.dumps(self.report), encoding='utf-8')
        self.settings = ot.TTSSettings(key=FAKE_KEY)
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.status_code = 200
        self.response.headers = {'Content-Type': 'audio/mpeg', 'Content-Length': str(len(MP3))}
        self.response.iter_content.side_effect = lambda **kwargs: iter((MP3[:65536], MP3[65536:]))
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.session.post.return_value = self.response
        self.factory = Mock(return_value=self.session)

    def provider(self):
        return ot.OpenAITTS(self.settings, session_factory=self.factory)

    def generate(self, name='spoken'):
        return ot.generate_once(self.report, self.settings, name, approved=True, session_factory=self.factory)

    def cli(self, *args):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            code = ot.main(['--report', str(self.report_path), *args])
        return code, json.loads(output.getvalue())

    def test_settings_defaults_and_no_key_in_repr(self):
        self.assertEqual(self.settings.model, 'gpt-4o-mini-tts')
        self.assertEqual(self.settings.voice, 'marin')
        self.assertEqual(self.settings.response_format, 'mp3')
        self.assertNotIn(FAKE_KEY, repr(self.settings))
        self.assertNotIn(FAKE_KEY, json.dumps(self.settings.public()))

    def test_settings_reject_unknown_model_voice_format_and_speed(self):
        for settings in (replace(self.settings, model='future-model'), replace(self.settings, voice='custom'),
                         replace(self.settings, response_format='wav'), replace(self.settings, speed=0),
                         replace(self.settings, speed=float('nan')), replace(self.settings, speed=True)):
            with self.subTest(settings=settings):
                with self.assertRaises(tg.BriefingError):
                    ot.OpenAITTS(settings, session_factory=self.factory).synthesize(self.script)
        self.factory.assert_not_called()

    def test_environment_settings_are_separate_and_validated_offline(self):
        with patch.dict('os.environ', {'ARVIS_TTS_VOICE': 'cedar', 'ARVIS_TTS_SPEED': '0.9', 'OPENAI_API_KEY': FAKE_KEY}):
            settings = ot.TTSSettings.from_environment()
        self.assertEqual(settings.voice, 'cedar')
        self.assertEqual(settings.speed, 0.9)
        with patch.dict('os.environ', {'ARVIS_TTS_SPEED': 'bad'}):
            with self.assertRaises(tg.BriefingError):
                ot.TTSSettings.from_environment()

    def test_request_matches_speech_api_and_one_call_maximum(self):
        provider = self.provider()
        audio = provider.synthesize(self.script)
        self.assertFalse(audio.is_test)
        self.assertEqual(audio.script_sha256, self.script.script_sha256)
        call = self.session.post.call_args
        self.assertEqual(call.args, ('https://api.openai.com/v1/audio/speech',))
        self.assertEqual(call.kwargs['json'], dict(model='gpt-4o-mini-tts', voice='marin',
                         input=self.script.text, instructions=ot.INSTRUCTIONS, response_format='mp3', speed=1.0))
        self.assertEqual(call.kwargs['headers'], {'Authorization': 'Bearer '+FAKE_KEY})
        self.assertTrue(call.kwargs['verify'])
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertTrue(call.kwargs['stream'])
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.mount.call_args.args[1].max_retries.total, 0)
        with self.assertRaisesRegex(tg.BriefingError, 'tts_already_attempted'):
            provider.synthesize(self.script)
        self.assertEqual(self.session.post.call_count, 1)

    def test_key_script_and_length_rejected_before_http(self):
        for settings, script in ((replace(self.settings, key=''), self.script),
                                 (self.settings, replace(self.script, script_sha256='bad')),
                                 (self.settings, replace(self.script, text='x'*2001,
                                  script_sha256=hashlib.sha256(('x'*2001).encode()).hexdigest()))):
            with self.assertRaises(tg.BriefingError):
                ot.OpenAITTS(settings, session_factory=self.factory).synthesize(script)
        self.factory.assert_not_called()

    def test_http_errors_are_static_no_raw_response_and_no_retry(self):
        for status, reason in ((400, 'tts_request_rejected'), (401, 'tts_authentication_failed'),
                               (403, 'tts_permission_denied'), (404, 'tts_model_unavailable'),
                               (429, 'tts_rate_or_quota_limited'), (500, 'tts_server_error'), (302, 'tts_api_failed')):
            self.response.status_code = status
            with self.assertRaisesRegex(tg.BriefingError, '^'+reason+'$'):
                self.provider().synthesize(self.script)
        self.response.json.assert_not_called()
        self.assertEqual(self.session.post.call_count, 7)

    def test_transport_exception_has_no_secret_and_is_not_retried(self):
        self.session.post.side_effect = RuntimeError(FAKE_KEY+FAKE_TOKEN+FAKE_CHAT)
        provider = self.provider()
        with self.assertRaisesRegex(tg.BriefingError, '^tts_transport_failed$'):
            provider.synthesize(self.script)
        with self.assertRaises(tg.BriefingError):
            provider.synthesize(self.script)
        self.assertEqual(self.session.post.call_count, 1)

    def test_transport_types_are_classified_without_exception_messages(self):
        secret = FAKE_KEY+FAKE_TOKEN+FAKE_CHAT
        cases = (
            (ot.requests.exceptions.ConnectionError(ot.socket.gaierror(-2,secret)), 'tts_dns_failed','gaierror'),
            (ot.requests.exceptions.ConnectionError(ConnectionRefusedError(secret)), 'tts_connection_failed','ConnectionError'),
            (ot.requests.exceptions.SSLError(ot.ssl.SSLCertVerificationError(1,secret)), 'tts_certificate_failed','SSLCertVerificationError'),
            (ot.requests.exceptions.SSLError(secret), 'tts_tls_failed','SSLError'),
            (ot.requests.exceptions.ConnectTimeout(secret), 'tts_connect_timeout','ConnectTimeout'),
            (ot.requests.exceptions.ReadTimeout(secret), 'tts_read_timeout','ReadTimeout'),
            (ot.requests.exceptions.ConnectionError(ot.ue.ReadTimeoutError(None,secret,secret)), 'tts_read_timeout','ReadTimeoutError'),
            (ot.requests.exceptions.ChunkedEncodingError(secret), 'tts_response_stream_failed','ChunkedEncodingError'),
            (ot.requests.exceptions.Timeout(secret), 'tts_timeout','Timeout'),
            (RuntimeError(secret), 'tts_transport_failed','RuntimeError'),
        )
        for error, reason, label in cases:
            self.session.post.side_effect = error
            provider = self.provider()
            with self.assertRaises(ot.TTSRequestError) as caught:
                provider.synthesize(self.script)
            self.assertEqual(str(caught.exception),reason)
            self.assertEqual(caught.exception.diagnostics,dict(http_status=None,api_error_code=None,exception_type=label,stage='request'))
            self.assertNotIn(FAKE_KEY,json.dumps(caught.exception.diagnostics))
            with self.assertRaisesRegex(tg.BriefingError,'tts_already_attempted'):
                provider.synthesize(self.script)
        self.assertEqual(self.session.post.call_count,len(cases))

    def test_wrapped_dns_reason_and_connect_timeout_classification(self):
        dns = ot.ue.NameResolutionError('untrusted.invalid',None,ot.socket.gaierror(-2,FAKE_KEY))
        wrapped = ot.requests.exceptions.ConnectionError(ot.ue.MaxRetryError(None,'untrusted',dns))
        self.assertEqual(str(ot.transport_error(wrapped,None)),'tts_dns_failed')
        timeout = ot.requests.exceptions.ConnectionError(ot.ue.ConnectTimeoutError(FAKE_KEY))
        self.assertEqual(str(ot.transport_error(timeout,None)),'tts_connect_timeout')

    def test_http_status_and_public_codes_distinguish_quota_rate_and_server(self):
        cases = ((400,None,'tts_request_rejected'),(401,'invalid_api_key','tts_authentication_failed'),
                 (403,None,'tts_permission_denied'),(404,'model_not_found','tts_model_unavailable'),
                 (429,'insufficient_quota','tts_quota_exhausted'),
                 (429,'credit_balance_exhausted','tts_quota_exhausted'),
                 (429,'project_spend_limit_exceeded','tts_quota_exhausted'),
                 (429,'organization_spend_limit_exceeded','tts_quota_exhausted'),
                 (429,'organization_usage_limit_exceeded','tts_quota_exhausted'),
                 (429,'rate_limit_exceeded','tts_rate_limited'),(429,'slow_down','tts_rate_limited'),
                 (429,None,'tts_rate_or_quota_limited'),
                 (500,'server_error','tts_server_error'),(503,'server_is_overloaded','tts_server_error'))
        for status, code, reason in cases:
            self.response.status_code = status
            body = json.dumps({'error':{'code':code,'message':FAKE_KEY+FAKE_TOKEN+FAKE_CHAT,
                                       'param':FAKE_KEY},'headers':{'Authorization':FAKE_KEY}}).encode()
            self.response.iter_content.side_effect = lambda **kwargs: iter((body,))
            with self.assertRaises(ot.TTSRequestError) as caught:
                self.provider().synthesize(self.script)
            self.assertEqual(str(caught.exception),reason)
            self.assertEqual(caught.exception.diagnostics['http_status'],status)
            self.assertEqual(caught.exception.diagnostics['api_error_code'],code)
            self.assertNotIn(FAKE_KEY,json.dumps(caught.exception.diagnostics))
        self.assertEqual(self.session.post.call_count,len(cases))

    def test_error_body_is_bounded_and_unknown_codes_never_escape(self):
        for body in (b'not-json',b'x'*16385,json.dumps({'error':{'code':FAKE_KEY,'message':FAKE_TOKEN}}).encode()):
            self.response.status_code = 429
            self.response.iter_content.side_effect = lambda **kwargs: iter((body,))
            with self.assertRaises(ot.TTSRequestError) as caught:
                self.provider().synthesize(self.script)
            self.assertIsNone(caught.exception.diagnostics['api_error_code'])
            self.assertEqual(str(caught.exception),'tts_rate_or_quota_limited')
        self.response.json.assert_not_called()

    def test_http_200_stream_timeout_and_invalid_format_have_response_status(self):
        self.response.iter_content.side_effect = ot.requests.exceptions.ConnectionError(ot.ue.ReadTimeoutError(None,FAKE_KEY,FAKE_KEY))
        with self.assertRaises(ot.TTSRequestError) as caught:
            self.provider().synthesize(self.script)
        self.assertEqual(str(caught.exception),'tts_read_timeout')
        self.assertEqual(caught.exception.diagnostics['http_status'],200)
        self.response.headers = {'Content-Type':'application/json'}
        with self.assertRaises(ot.TTSRequestError) as caught:
            self.provider().synthesize(self.script)
        self.assertEqual(str(caught.exception),'tts_response_invalid')
        self.assertEqual(caught.exception.diagnostics['http_status'],200)
        self.response.headers = {'Content-Type':'audio/mpeg'}
        self.response.iter_content.side_effect = lambda **kwargs: iter((b'broken mp3',))
        with self.assertRaises(ot.TTSRequestError) as caught:
            self.provider().synthesize(self.script)
        self.assertEqual(str(caught.exception),'invalid_audio')
        self.assertEqual(caught.exception.diagnostics['exception_type'],'ResponseValidationError')

    def test_cli_and_receipt_record_safe_failure_only_and_block_retry(self):
        self.session.post.side_effect = ot.requests.exceptions.SSLError(FAKE_KEY+FAKE_TOKEN+FAKE_CHAT)
        with patch.dict('os.environ',{'OPENAI_API_KEY':FAKE_KEY}),patch.object(ot.requests,'Session',self.factory):
            code, result = self.cli('--name','tls-failure','--generate','--approve-tts')
        self.assertEqual(code,1)
        self.assertEqual(result['reason'],'tts_tls_failed')
        self.assertEqual(result['exception_type'],'SSLError')
        receipt_path = tg.REPORTS/'tls-failure.tts-attempt.json'
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['failure']['exception_type'],'SSLError')
        self.assertEqual(receipt['status'],'failed_or_unknown')
        for secret in (FAKE_KEY,FAKE_TOKEN,FAKE_CHAT):
            self.assertNotIn(secret,json.dumps(result)+json.dumps(receipt))
        with self.assertRaises(tg.BriefingError):
            self.generate('tls-failure')
        self.assertEqual(self.session.post.call_count,1)

    def test_local_postprocessing_valueerror_is_not_misreported_as_network(self):
        with patch.object(vb,'scan_audio',side_effect=ValueError(FAKE_KEY)):
            with self.assertRaises(ot.TTSRequestError) as caught:
                self.provider().synthesize(self.script)
        self.assertEqual(str(caught.exception),'tts_response_invalid')
        self.assertEqual(caught.exception.diagnostics['exception_type'],'ValueError')
        self.assertEqual(caught.exception.diagnostics['stage'],'audio_privacy')
        self.assertEqual(caught.exception.diagnostics['received_bytes'],len(MP3))

    def test_bounded_audio_content_type_container_and_duration(self):
        cases = [({'Content-Type': 'application/json'}, [MP3]),
                 ({'Content-Type': 'audio/mpeg', 'Content-Length': str(vb.MAX_AUDIO+1)}, []),
                 ({'Content-Type': 'audio/mpeg'}, [bytes(vb.MAX_AUDIO), b'x']),
                 ({'Content-Type': 'audio/mpeg'}, [b'not mp3']),
                 ({'Content-Type': 'audio/mpeg'}, [(b'\xff\xfb\x90\x00'+bytes(413))*40])]
        for headers, chunks in cases:
            self.response.headers = headers
            self.response.iter_content.side_effect = lambda **kwargs: iter(chunks)
            with self.assertRaises(tg.BriefingError):
                self.provider().synthesize(self.script)

    def test_compressed_payload_is_not_mistaken_for_text_paths(self):
        payload = b'C:\\ordinary https://untrusted.invalid/'
        data = (MP3[:4]+payload+bytes(413-len(payload)))*2500
        # The old text scanner falsely rejected this structurally valid MP3.
        with self.assertRaises(ValueError):
            vb.safe(data.decode('utf-8',errors='ignore'))
        self.response.headers = {'Content-Type':'audio/mpeg'}
        self.response.iter_content.side_effect = lambda **kwargs: iter((data,))
        script, audio = self.generate('opaque')
        self.assertEqual(ot.load_artifacts(self.report,'opaque'),(script,audio))
        self.assertEqual(vb.mp3_duration(audio.data),65.306)

    def test_voice_only_accepts_opaque_mp3_and_explicit_synthetic_caption(self):
        payload = b'C:\\ordinary https://untrusted.invalid/'
        data = (MP3[:4]+payload+bytes(413-len(payload)))*2500
        audio = vb.AudioArtifact(self.script.report_schema_version,self.script.report_reference,
                                 self.script.script_sha256,self.settings.provider_id,data,False)
        config = tg.Configuration(FAKE_TOKEN,FAKE_CHAT,(FAKE_CHAT,))
        telegram = MagicMock()
        telegram.__enter__.return_value = telegram
        telegram.post.return_value = Mock(status_code=200,json=Mock(return_value=dict(
            ok=True,result=dict(message_id=1,chat=dict(id=int(FAKE_CHAT),type='private')))))
        result = tg.deliver(self.report,tg.summary(self.report),None,config,voice=(self.script,audio),
                            voice_only=True,session_factory=Mock(return_value=telegram))
        self.assertTrue(result['voice_sent'])
        self.assertFalse(result['message_sent'])
        self.assertEqual(telegram.post.call_count,1)
        self.assertIn('합성 테스트 데이터',telegram.post.call_args.kwargs['data']['caption'])

    def test_actual_secret_bytes_are_still_rejected(self):
        for secret in (FAKE_KEY,FAKE_TOKEN,FAKE_CHAT):
            payload = secret.encode()
            data = (MP3[:4]+payload+bytes(413-len(payload)))*2500
            with self.assertRaisesRegex(tg.BriefingError,'sensitive_content_rejected'):
                vb.scan_audio(data,(secret,))
        self.response.headers = {'Content-Type':'audio/mpeg'}
        payload = FAKE_KEY.encode()
        data = (MP3[:4]+payload+bytes(413-len(payload)))*2500
        self.response.iter_content.side_effect = lambda **kwargs: iter((data,))
        with self.assertRaises(ot.TTSRequestError) as caught:
            self.provider().synthesize(self.script)
        self.assertEqual(str(caught.exception),'sensitive_content_rejected')
        self.assertEqual(caught.exception.diagnostics['stage'],'audio_privacy')
        self.assertNotIn(FAKE_KEY,json.dumps(caught.exception.diagnostics))

    def test_bare_mpeg_and_id3_headers_preserve_frame_duration(self):
        tag = bytes(12)
        for major, flags in ((3,0),(4,0),(4,16)):
            header = b'ID3'+bytes((major,0,flags,0,0,0,len(tag)))
            data = header+tag+(b'3DI'+header[3:] if flags else b'')+MP3
            self.assertEqual(vb.mp3_duration(data),vb.mp3_duration(MP3))
            self.response.headers = {'Content-Type':'audio/mpeg'}
            self.response.iter_content.side_effect = lambda **kwargs: iter((data,))
            self.assertEqual(self.provider().synthesize(self.script).data,data)
        mpeg2 = (b'\xff\xf3\x84\x00'+bytes(188))*3000
        self.assertEqual(vb.mp3_duration(mpeg2),72.0)

    def test_bad_headers_corrupt_frames_and_content_length_have_safe_stages(self):
        bad_audio = (b'not mp3',MP3[:-1],b'ID3\x04\x00\x00\x80\x00\x00\x00'+MP3,
                     b'ID3\x04\x00\x00\x7f\x7f\x7f\x7f'+MP3)
        for data in bad_audio:
            self.response.headers = {'Content-Type':'audio/mpeg'}
            self.response.iter_content.side_effect = lambda **kwargs: iter((data,))
            with self.assertRaises(ot.TTSRequestError) as caught:
                self.provider().synthesize(self.script)
            diagnostics = caught.exception.diagnostics
            self.assertEqual(str(caught.exception),'invalid_audio')
            self.assertEqual(diagnostics['stage'],'mp3_structure')
            self.assertEqual(diagnostics['received_bytes'],len(data))
            self.assertIn(diagnostics['mp3_header'],('id3','mpeg_frame','unknown'))
            self.assertNotIn('data',diagnostics)
        for length in ('²','9'*5000):
            self.response.headers = {'Content-Type':'audio/mpeg','Content-Length':length}
            with self.assertRaises(ot.TTSRequestError) as caught:
                self.provider().synthesize(self.script)
            self.assertEqual(caught.exception.diagnostics['stage'],'response_headers')
            self.assertEqual(caught.exception.diagnostics['exception_type'],'ResponseValidationError')

    def test_long_valid_audio_records_duration_without_relaxing_target(self):
        data = (MP3[:417])*7000
        with self.assertRaises(tg.BriefingError):
            vb.mp3_duration(data)  # Preserve the generic 180 second ceiling.
        measured = vb.mp3_duration(data,max_seconds=None)
        self.assertGreater(measured,180)
        self.response.headers = {'Content-Type':'audio/mpeg'}
        self.response.iter_content.side_effect = lambda **kwargs: iter((data,))
        with patch.dict('os.environ',{'OPENAI_API_KEY':FAKE_KEY}),patch.object(ot.requests,'Session',self.factory):
            code, result = self.cli('--name','too-long','--generate','--approve-tts')
        self.assertEqual(code,1)
        self.assertEqual(result['reason'],'tts_duration_outside_target')
        self.assertEqual(result['duration_seconds'],measured)
        self.assertEqual(result['stage'],'duration_target')
        self.assertFalse((tg.REPORTS/'too-long.mp3').exists())
        receipt=json.loads((tg.REPORTS/'too-long.tts-attempt.json').read_text())
        self.assertEqual(receipt['failure']['duration_seconds'],measured)

    def test_duration_rejection_preserves_measured_seconds_without_audio_or_retry(self):
        short = (b'\xff\xfb\x90\x00'+bytes(413))*40
        self.response.headers = {'Content-Type':'audio/mpeg'}
        self.response.iter_content.side_effect = lambda **kwargs: iter((short,))
        with patch.dict('os.environ', {'OPENAI_API_KEY':FAKE_KEY}), patch.object(ot.requests,'Session',self.factory):
            code, result = self.cli('--name','short','--generate','--approve-tts')
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'tts_duration_outside_target')
        self.assertEqual(result['duration_seconds'], vb.mp3_duration(short))
        self.assertEqual((result['minimum_seconds'],result['maximum_seconds']), (60,120))
        receipt_path = tg.REPORTS/'short.tts-attempt.json'
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['status'], 'rejected_duration')
        self.assertEqual(receipt['duration_seconds'], result['duration_seconds'])
        self.assertNotIn(FAKE_KEY,json.dumps(result)+json.dumps(receipt))
        self.assertFalse((tg.REPORTS/'short.mp3').exists())
        with self.assertRaises(tg.BriefingError):
            self.generate('short')
        self.assertEqual(self.session.post.call_count,1)

    def test_estimate_does_not_replace_measured_mp3_duration(self):
        self.assertTrue(60 <= self.script.estimated_seconds <= 120)
        long_audio = (b'\xff\xfb\x90\x00'+bytes(413))*5000
        self.response.headers = {'Content-Type':'audio/mpeg'}
        self.response.iter_content.side_effect = lambda **kwargs: iter((long_audio,))
        with self.assertRaises(ot.DurationTargetError) as caught:
            self.provider().synthesize(self.script)
        self.assertEqual(caught.exception.duration_seconds, vb.mp3_duration(long_audio))
        self.assertGreater(caught.exception.duration_seconds,120)

    def test_generation_requires_approval_and_preserves_existing_files(self):
        with self.assertRaisesRegex(tg.BriefingError, 'tts_approval_required'):
            ot.generate_once(self.report, self.settings, 'spoken', session_factory=self.factory)
        (tg.REPORTS/'spoken.mp3').write_bytes(b'preserve')
        with self.assertRaisesRegex(tg.BriefingError, 'existing_artifact_preserved'):
            self.generate()
        self.factory.assert_not_called()
        self.assertEqual((tg.REPORTS/'spoken.mp3').read_bytes(), b'preserve')

    def test_generate_load_binding_and_receipt(self):
        script, audio = self.generate()
        self.assertEqual(ot.load_artifacts(self.report, 'spoken'), (script, audio))
        for path in tg.REPORTS.iterdir():
            if path.suffix == '.json':
                text = path.read_text(encoding='utf-8')
                for secret in (FAKE_KEY, FAKE_TOKEN, FAKE_CHAT, str(self.root)):
                    self.assertNotIn(secret, text)
        with self.assertRaises(tg.BriefingError):
            self.generate()
        self.assertEqual(self.session.post.call_count, 1)

    def test_failed_generation_retains_reservation_no_text_fallback(self):
        self.session.post.side_effect = RuntimeError(FAKE_KEY)
        with patch.object(tg, 'deliver', side_effect=AssertionError('send')):
            with self.assertRaises(tg.BriefingError):
                self.generate()
            with self.assertRaises(tg.BriefingError):
                self.generate()
        self.assertEqual(self.session.post.call_count, 1)
        self.assertFalse((tg.REPORTS/'spoken.mp3').exists())
        self.assertEqual(json.loads((tg.REPORTS/'spoken.tts-attempt.json').read_text())['status'], 'failed_or_unknown')

    def test_tampered_audio_report_or_receipt_blocked(self):
        self.generate()
        other = deepcopy(self.report)
        other['generated_at'] = '2026-10-29T10:01:00+09:00'
        other['evaluation_reference'] = tg.digest({k:v for k,v in other.items() if k!='evaluation_reference'})
        with self.assertRaises(tg.BriefingError):
            ot.load_artifacts(other, 'spoken')
        receipt_path = tg.REPORTS/'spoken.tts-attempt.json'
        original = receipt_path.read_bytes()
        receipt_path.write_text('{}', encoding='utf-8')
        with self.assertRaises(tg.BriefingError):
            ot.load_artifacts(self.report, 'spoken')
        receipt_path.write_bytes(original)
        (tg.REPORTS/'spoken.mp3').write_bytes(MP3+b'x')
        with self.assertRaisesRegex(tg.BriefingError, 'audio_integrity_failed'):
            ot.load_artifacts(self.report, 'spoken')

    def test_fixtures_are_not_loadable_as_real_speech(self):
        vb.write_artifacts(self.report, self.script, vb.SilenceFixtureTTS().synthesize(self.script), 'fixture')
        (tg.REPORTS/'fixture.tts-attempt.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(tg.BriefingError, 'real_audio_required'):
            ot.load_artifacts(self.report, 'fixture')

    def test_default_cli_no_network_no_writes_or_credentials_in_output(self):
        with patch.object(ot.requests, 'Session', side_effect=AssertionError('HTTP')), patch.dict('os.environ', {'OPENAI_API_KEY': FAKE_KEY}):
            code, result = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'dry_run')
        self.assertFalse(result['network_called'])
        self.assertIn('합성', result['script']['text'])
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertEqual(len(list(tg.REPORTS.iterdir())), 1)

    def test_dry_run_validates_saved_audio_without_network(self):
        self.generate()
        with patch.object(ot.requests, 'Session', side_effect=AssertionError('HTTP')):
            code, result = self.cli('--name', 'spoken')
        self.assertEqual(code, 0)
        self.assertTrue(result['audio_validated'])
        self.assertTrue(60 <= result['duration_seconds'] <= 120)
        self.assertFalse(result['generation_name_available'])

    def test_cli_generate_calls_tts_only_and_send_invalid_audio_never_falls_back(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': FAKE_KEY}), \
             patch.object(ot.requests, 'Session', self.factory), \
             patch.object(tg, 'deliver', side_effect=AssertionError('Telegram')):
            code, result = self.cli('--name', 'generated-cli', '--generate', '--approve-tts')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'generated')
        (tg.REPORTS/'generated-cli.mp3').write_bytes(b'bad mp3')
        with patch.object(tg.Configuration, 'from_environment', return_value=tg.Configuration(FAKE_TOKEN,FAKE_CHAT,(FAKE_CHAT,))), \
             patch.object(tg, 'deliver', side_effect=AssertionError('Telegram')):
            code, result = self.cli('--name', 'generated-cli', '--send-voice', '--approve-telegram')
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'audio_integrity_failed')

    def test_http_debug_settings_restored_and_raw_auth_never_logged(self):
        import http.client
        import logging
        wire = logging.getLogger('urllib3.connectionpool')
        before = wire.disabled
        debug = http.client.HTTPConnection.debuglevel
        def fake_post(*args, **kwargs):
            self.assertTrue(wire.disabled)
            self.assertEqual(http.client.HTTPConnection.debuglevel, 0)
            return self.response
        self.session.post.side_effect = fake_post
        self.provider().synthesize(self.script)
        self.assertEqual(wire.disabled, before)
        self.assertEqual(http.client.HTTPConnection.debuglevel, debug)

    def test_cli_does_not_echo_unknown_exception_or_secret_artifact_name(self):
        with patch.object(ot, 'paths_for', side_effect=RuntimeError(FAKE_KEY+str(self.root))):
            code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'voice_validation_failed')
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        with patch.dict('os.environ', {'ARVIS_TELEGRAM_CHAT_ID': FAKE_CHAT}):
            code, result = self.cli('--name', FAKE_CHAT)
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_CHAT, json.dumps(result))

    def test_cli_approvals_are_independent_and_never_fallback(self):
        for arguments in (('--generate',), ('--send-voice',), ('--approve-tts',),
                          ('--generate', '--approve-telegram'), ('--send-voice', '--approve-tts')):
            with patch.object(ot.requests, 'Session', side_effect=AssertionError('HTTP')), patch.object(tg, 'deliver', side_effect=AssertionError('send')):
                code, result = self.cli(*arguments)
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'rejected')

    def test_send_cli_loads_one_artifact_without_openai_key_or_call(self):
        self.generate()
        config = tg.Configuration(FAKE_TOKEN, FAKE_CHAT, (FAKE_CHAT,))
        with patch.object(tg.Configuration, 'from_environment', return_value=config), \
             patch.object(ot.OpenAITTS, 'synthesize', side_effect=AssertionError('paid TTS')), \
             patch.object(tg, 'deliver', return_value={'status':'sent'}) as deliver:
            code, result = self.cli('--name', 'spoken', '--send-voice', '--approve-telegram')
        self.assertEqual(code, 0)
        self.assertTrue(deliver.call_args.kwargs['voice_only'])
        self.assertIsNone(deliver.call_args.args[2])
        self.assertFalse(deliver.call_args.kwargs['voice'][1].is_test)

    def test_voice_only_single_call_independent_from_text_reservation(self):
        script, audio = self.generate()
        config = tg.Configuration(FAKE_TOKEN, FAKE_CHAT, (FAKE_CHAT,))
        telegram = MagicMock()
        telegram.__enter__.return_value = telegram
        telegram.post.return_value = Mock(status_code=200, json=Mock(return_value=dict(
            ok=True, result=dict(message_id=1, chat=dict(id=int(FAKE_CHAT), type='private')))))
        factory = Mock(return_value=telegram)
        tg.deliver(self.report, tg.summary(self.report), None, config, session_factory=factory)
        text_state = {p.name:p.read_bytes() for p in tg.STATE.iterdir()}
        result = tg.deliver(self.report, tg.summary(self.report), None, config,
                            voice=(script,audio), voice_only=True, session_factory=factory)
        self.assertTrue(result['voice_sent'])
        self.assertFalse(result['message_sent'])
        self.assertFalse(result['document_sent'])
        self.assertEqual(telegram.post.call_count, 2)
        self.assertTrue(telegram.post.call_args.args[0].endswith('/sendVoice'))
        for name, value in text_state.items():
            self.assertEqual((tg.STATE/name).read_bytes(), value)
        result = tg.deliver(self.report, tg.summary(self.report), None, config,
                            voice=(script,audio), voice_only=True, voice_method='sendAudio', session_factory=factory)
        self.assertEqual(result['status'], 'duplicate_blocked')
        self.assertEqual(telegram.post.call_count, 2)

    def test_voice_only_failure_is_reserved_no_text_or_retry(self):
        script, audio = self.generate()
        config = tg.Configuration(FAKE_TOKEN, FAKE_CHAT, (FAKE_CHAT,))
        telegram = MagicMock()
        telegram.__enter__.return_value = telegram
        telegram.post.side_effect = RuntimeError(FAKE_TOKEN)
        factory = Mock(return_value=telegram)
        result = tg.deliver(self.report, tg.summary(self.report), None, config,
                            voice=(script,audio), voice_only=True, session_factory=factory)
        self.assertEqual(result['status'], 'failed_or_unknown')
        self.assertFalse(result['message_sent'])
        self.assertNotIn(FAKE_TOKEN, json.dumps(result))
        self.assertEqual(tg.deliver(self.report, tg.summary(self.report), None, config,
                         voice=(script,audio), voice_only=True, session_factory=factory)['status'], 'duplicate_blocked')
        self.assertEqual(telegram.post.call_count, 1)

    def test_prior_bundled_voice_or_ambiguous_delivery_blocks_voice_only(self):
        script, audio = self.generate()
        config = tg.Configuration(FAKE_TOKEN, FAKE_CHAT, (FAKE_CHAT,))
        telegram = MagicMock()
        telegram.__enter__.return_value = telegram
        telegram.post.return_value = Mock(status_code=200, json=Mock(return_value=dict(
            ok=True, result=dict(message_id=1, chat=dict(id=int(FAKE_CHAT), type='private')))))
        factory = Mock(return_value=telegram)
        tg.deliver(self.report, tg.summary(self.report), None, config,
                   voice=(script,audio), session_factory=factory)
        for status, voice_sent in (('sent',True), ('reserved',False), ('message_sent',False), ('failed_or_unknown',False)):
            path = next(tg.STATE.iterdir())
            previous = json.loads(path.read_text())
            previous.update(status=status, voice_sent=voice_sent)
            path.write_text(json.dumps(previous), encoding='utf-8')
            result = tg.deliver(self.report, tg.summary(self.report), None, config,
                                voice=(script,audio), voice_only=True, session_factory=factory)
            self.assertEqual(result['status'], 'duplicate_blocked')
        self.assertEqual(telegram.post.call_count, 2)
        self.assertEqual(len(list(tg.STATE.iterdir())), 1)

    def test_voice_only_missing_test_audio_or_html_rejected_before_network(self):
        config = tg.Configuration(FAKE_TOKEN, FAKE_CHAT, (FAKE_CHAT,))
        audio = vb.SilenceFixtureTTS().synthesize(self.script)
        for kwargs in ({}, {'voice':(self.script,audio)}):
            with self.assertRaises(tg.BriefingError):
                tg.deliver(self.report, tg.summary(self.report), None, config,
                           voice_only=True, session_factory=self.factory, **kwargs)
        self.factory.assert_not_called()
        self.assertFalse(tg.STATE.exists())

    def test_old_voice_cli_cannot_fallback_to_live_text(self):
        with patch.object(tg.Configuration, 'from_environment', return_value=tg.Configuration(FAKE_TOKEN,FAKE_CHAT,(FAKE_CHAT,))), \
             patch.object(tg, 'deliver', side_effect=AssertionError('fallback')), \
             patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(vb.main(['--report',str(self.report_path),'--send']), 1)
            self.assertEqual(vb.main(['--report',str(self.report_path),'--send','--provider','fixture']), 1)


if __name__ == '__main__':
    unittest.main()
