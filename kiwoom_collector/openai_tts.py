"""One-shot Korean Speech API adapter and separately approved voice delivery.

No SDK dependency: use the documented SDK request schema over bounded HTTPS.
The default mode never creates an HTTP session or writes an artifact.
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import http.client
import json
import logging
import math
import os
from pathlib import Path
import re

import requests
from requests.adapters import HTTPAdapter

from . import telegram_briefing as tg, voice_briefing as vb

MODELS = ('gpt-4o-mini-tts', 'gpt-4o-mini-tts-2025-12-15')
VOICES = ('alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx', 'nova',
          'sage', 'shimmer', 'verse', 'marin', 'cedar')
MAX_SCRIPT_CHARS = 2000
INSTRUCTIONS = ('차분하고 자연스러운 한국어 뉴스 브리핑으로 읽으세요. '
                '입력 원고만 그대로 읽고 사실, 종목, 숫자, 설명을 추가하거나 생략하지 마세요. '
                '종목 코드의 각 자리와 한글로 풀어 쓴 숫자, 금액, 시각을 또렷하게 읽으세요. '
                '과장하거나 매매를 권유하지 말고 적절히 쉬어 읽으세요. 목표 길이는 일 분에서 이 분입니다.')
ERROR_CODES = frozenset((
    'tts_configuration_invalid', 'tts_key_required', 'tts_model_unsupported',
    'tts_voice_unsupported', 'tts_format_unsupported', 'tts_speed_invalid',
    'tts_script_too_long', 'tts_already_attempted', 'tts_authentication_failed',
    'tts_permission_denied', 'tts_model_unavailable', 'tts_rate_or_quota_limited',
    'tts_request_rejected', 'tts_api_failed', 'tts_transport_failed',
    'tts_response_invalid', 'tts_audio_too_large', 'tts_duration_outside_target',
    'tts_approval_required', 'telegram_approval_required', 'existing_artifact_preserved',
    'invalid_name', 'invalid_audio', 'audio_report_mismatch', 'speech_report_mismatch',
    'real_audio_required', 'audio_integrity_failed', 'tts_receipt_invalid',
    'configuration_required', 'private_chat_allowlist_required', 'recipient_not_allowed',
    'sensitive_content_rejected', 'invalid_execution_mode',
))


@dataclass(frozen=True)
class TTSSettings:
    model: str = MODELS[0]
    voice: str = 'marin'
    response_format: str = 'mp3'
    speed: float = 1.0
    key: str = field(default='', repr=False)

    @classmethod
    def from_environment(cls):
        try:
            result = cls(os.environ.get('ARVIS_TTS_MODEL', MODELS[0]).strip(),
                         os.environ.get('ARVIS_TTS_VOICE', 'marin').strip(),
                         os.environ.get('ARVIS_TTS_FORMAT', 'mp3').strip(),
                         float(os.environ.get('ARVIS_TTS_SPEED', '1.0')),
                         os.environ.get('OPENAI_API_KEY', '').strip())
        except (TypeError, ValueError):
            raise tg.BriefingError('tts_configuration_invalid') from None
        result.validate()
        return result

    def validate(self, *, require_key=False):
        tg.require(self.model in MODELS, 'tts_model_unsupported')
        tg.require(self.voice in VOICES, 'tts_voice_unsupported')
        tg.require(self.response_format == 'mp3', 'tts_format_unsupported')
        tg.require(type(self.speed) in (int, float) and math.isfinite(self.speed)
                   and 0.25 <= self.speed <= 4.0, 'tts_speed_invalid')
        if require_key:
            tg.require(isinstance(self.key, str) and bool(re.fullmatch(r'[!-~]{1,512}', self.key)), 'tts_key_required')

    @property
    def provider_id(self):
        return 'openai-'+self.model+'-'+self.voice

    def public(self):
        return dict(model=self.model, voice=self.voice, response_format=self.response_format,
                    speed=self.speed, api_key_present=bool(self.key),
                    billing='input_text_tokens_and_output_audio_tokens',
                    input_usd_per_million=0.60, output_audio_usd_per_million=12.0,
                    model_shutdown_date='2027-01-06', max_http_calls=1)


@contextmanager
def quiet_http():
    """Never expose authorization headers, URL credentials or request bodies."""
    names = ('urllib3.connectionpool', 'requests.packages.urllib3.connectionpool')
    loggers = [logging.getLogger(name) for name in names]
    previous = [logger.disabled for logger in loggers]
    debug = http.client.HTTPConnection.debuglevel
    try:
        for logger in loggers:
            logger.disabled = True
        http.client.HTTPConnection.debuglevel = 0
        yield
    finally:
        for logger, disabled in zip(loggers, previous):
            logger.disabled = disabled
        http.client.HTTPConnection.debuglevel = debug


class OpenAITTS:
    """One request maximum per provider instance; no paid retries or fallback."""
    def __init__(self, settings, *, session_factory=None):
        self.settings = settings
        self.session_factory = session_factory or requests.Session
        self.attempted = False

    def synthesize(self, script):
        settings = self.settings
        settings.validate(require_key=True)
        validate_script(script, (settings.key,))
        tg.require(not self.attempted, 'tts_already_attempted')
        self.attempted = True
        payload = dict(model=settings.model, voice=settings.voice, input=script.text,
                       instructions=INSTRUCTIONS, response_format=settings.response_format,
                       speed=settings.speed)
        try:
            with quiet_http(), self.session_factory() as session:
                session.trust_env = False
                session.mount('https://', HTTPAdapter(max_retries=0))
                with session.post('https://api.openai.com/v1/audio/speech',
                                  headers={'Authorization': 'Bearer '+settings.key}, json=payload,
                                  timeout=(5, 90), allow_redirects=False, verify=True, stream=True) as response:
                    failures = {400: 'tts_request_rejected', 401: 'tts_authentication_failed',
                                403: 'tts_permission_denied', 404: 'tts_model_unavailable',
                                429: 'tts_rate_or_quota_limited'}
                    tg.require(response.status_code == 200,
                               failures.get(response.status_code, 'tts_api_failed'))
                    mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                    tg.require(mime in ('audio/mpeg', 'audio/mp3'), 'tts_response_invalid')
                    content_length = response.headers.get('Content-Length')
                    if content_length is not None:
                        tg.require(content_length.isdigit(), 'tts_response_invalid')
                        tg.require(int(content_length) <= vb.MAX_AUDIO, 'tts_audio_too_large')
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        tg.require(isinstance(chunk, bytes), 'tts_response_invalid')
                        tg.require(len(data)+len(chunk) <= vb.MAX_AUDIO, 'tts_audio_too_large')
                        data.extend(chunk)
            audio = vb.AudioArtifact(script.report_schema_version, script.report_reference,
                                     script.script_sha256, settings.provider_id, bytes(data), False)
            duration = vb.mp3_duration(audio.data)
            tg.require(60 <= duration <= 120, 'tts_duration_outside_target')
            vb.safe(audio.data.decode('utf-8', errors='ignore'))
            tg.scan(audio.data.decode('utf-8', errors='ignore'), (settings.key,))
            return audio
        except tg.BriefingError:
            raise
        except Exception:
            raise tg.BriefingError('tts_transport_failed') from None


def validate_script(script, secrets=()):
    tg.require(isinstance(script, vb.SpeechScript), 'speech_report_mismatch')
    tg.require(script.script_sha256 == hashlib.sha256(script.text.encode()).hexdigest()
               and script.candidate_confirmed is False, 'speech_report_mismatch')
    tg.require(0 < len(script.text) <= MAX_SCRIPT_CHARS, 'tts_script_too_long')
    vb.safe(asdict(script))
    tg.scan(script.text, secrets)


def paths_for(name):
    tg.require(isinstance(name, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name)), 'invalid_name')
    root = tg.REPORTS.resolve()
    tg.require(root == tg.PROJECT.resolve()/'reports', 'invalid_report_root')
    return tuple(root/(name+suffix) for suffix in ('.speech.json', '.mp3', '.tts-attempt.json'))


def generate_once(report, settings, name, *, approved=False, session_factory=None):
    tg.require(approved is True, 'tts_approval_required')
    settings.validate(require_key=True)
    script = vb.build_script(report)
    secrets = tuple(os.environ.get(k, '').strip() for k in
                    ('OPENAI_API_KEY', 'ARVIS_TELEGRAM_BOT_TOKEN', 'ARVIS_TELEGRAM_CHAT_ID'))+(settings.key,)
    tg.validate(report, secrets)
    validate_script(script, secrets)
    metadata_path, audio_path, receipt_path = paths_for(name)
    tg.require(not any(p.exists() or p.is_symlink() for p in (metadata_path, audio_path, receipt_path)),
               'existing_artifact_preserved')
    receipt_path.parent.mkdir(exist_ok=True)
    receipt = dict(status='reserved', report_reference=script.report_reference,
                   script_sha256=script.script_sha256, provider=settings.provider_id)
    # Reservation survives failures and process interruption. A repeated name cannot charge again.
    with receipt_path.open('x+', encoding='utf-8') as stream:
        def save():
            stream.seek(0)
            json.dump(receipt, stream)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        save()
        audio = OpenAITTS(settings, session_factory=session_factory).synthesize(script)
        vb.validate_audio(report, script, audio)
        vb.write_artifacts(report, script, audio, name)
        receipt.update(status='generated', audio_sha256=hashlib.sha256(audio.data).hexdigest())
        save()
    return script, audio


def load_artifacts(report, name, *, secrets=()):
    metadata_path, audio_path, receipt_path = paths_for(name)
    metadata = json.loads(tg.read_report(metadata_path, '.json', tg.MAX_JSON), object_pairs_hook=tg.unique_object)
    receipt = json.loads(tg.read_report(receipt_path, '.json', tg.MAX_JSON), object_pairs_hook=tg.unique_object)
    vb.safe(metadata)
    vb.safe(receipt)
    tg.scan(metadata, secrets)
    tg.scan(receipt, secrets)
    script = vb.build_script(report)
    tg.keys(metadata, ('script', 'audio'))
    tg.require(metadata['script'] == asdict(script), 'speech_report_mismatch')
    info = metadata['audio']
    tg.keys(info, ('provider', 'is_test', 'format', 'duration_seconds', 'sha256'))
    providers = {'openai-'+model+'-'+voice for model in MODELS for voice in VOICES}
    tg.require(info['provider'] in providers and info['is_test'] is False and info['format'] == 'mp3', 'real_audio_required')
    tg.require(audio_path.resolve().is_relative_to(tg.REPORTS.resolve()), 'report_path_rejected')
    with audio_path.open('rb') as stream:
        data = stream.read(vb.MAX_AUDIO+1)
    tg.require(hashlib.sha256(data).hexdigest() == info['sha256'], 'audio_integrity_failed')
    audio = vb.AudioArtifact(script.report_schema_version, script.report_reference, script.script_sha256,
                             info['provider'], data, False)
    duration = vb.validate_audio(report, script, audio)
    tg.require(60 <= duration <= 120 and duration == info['duration_seconds'], 'tts_duration_outside_target')
    tg.require(receipt == dict(status='generated', report_reference=script.report_reference,
                              script_sha256=script.script_sha256, provider=audio.provider,
                              audio_sha256=info['sha256']), 'tts_receipt_invalid')
    tg.scan(data.decode('utf-8', errors='ignore'), secrets)
    return script, audio


def main(argv=None):
    parser = argparse.ArgumentParser(description='Korean TTS: offline preview, approved generation, approved voice-only send')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--name', default='korean-briefing-once')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--generate', action='store_true')
    modes.add_argument('--send-voice', action='store_true')
    parser.add_argument('--approve-tts', action='store_true')
    parser.add_argument('--approve-telegram', action='store_true')
    parser.add_argument('--method', choices=('sendVoice', 'sendAudio'), default='sendVoice')
    args = parser.parse_args(argv)
    try:
        tg.require(not args.approve_tts or args.generate, 'invalid_execution_mode')
        tg.require(not args.approve_telegram or args.send_voice, 'invalid_execution_mode')
        secrets = tuple(os.environ.get(k, '').strip() for k in
                        ('OPENAI_API_KEY', 'ARVIS_TELEGRAM_BOT_TOKEN', 'ARVIS_TELEGRAM_CHAT_ID'))
        report, text, _ = tg.prepare(args.report, secrets=secrets)
        script = vb.build_script(report)
        validate_script(script, secrets)
        paths = paths_for(args.name)
        tg.scan([p.name for p in paths], secrets)
        if args.send_voice:
            tg.require(args.approve_telegram, 'telegram_approval_required')
            config = tg.Configuration.from_environment()
            script, audio = load_artifacts(report, args.name, secrets=secrets)
            result = tg.deliver(report, text, None, config, voice=(script, audio),
                                voice_method=args.method, voice_only=True)
        else:
            settings = TTSSettings.from_environment()
            if args.generate:
                tg.require(args.approve_tts, 'tts_approval_required')
                script, audio = generate_once(report, settings, args.name, approved=True)
                result = dict(status='generated', duration_seconds=vb.mp3_duration(audio.data),
                              candidate_confirmed=False, telegram_called=False)
            else:
                audio = None
                if all(p.exists() for p in paths):
                    _, audio = load_artifacts(report, args.name, secrets=secrets)
                result = dict(status='dry_run', settings=settings.public(), script=asdict(script),
                              artifacts_present=all(p.exists() for p in paths),
                              audio_validated=audio is not None,
                              duration_seconds=vb.mp3_duration(audio.data) if audio else None,
                              generation_name_available=not any(p.exists() or p.is_symlink() for p in paths),
                              artifact_names=[p.name for p in paths], network_called=False,
                              candidate_confirmed=False)
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result['status'] in ('dry_run', 'generated', 'sent') else 1
    except Exception as error:
        reason = str(error) if isinstance(error, tg.BriefingError) and str(error) in ERROR_CODES else 'voice_validation_failed'
        print(json.dumps(dict(status='rejected', reason=reason)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
