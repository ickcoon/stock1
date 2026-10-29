"""Step 7-C6 synthetic reports only; no live API, credentials or operating DB."""
from copy import deepcopy
import hashlib
from html import unescape
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from kiwoom_collector import am_briefing as b, am_momentum as am
from kiwoom_collector import telegram_briefing as tg, openai_tts as ot

FIXTURE = json.loads((Path(__file__).parent/'fixtures/am_sector_briefing_synthetic.json').read_text(encoding='utf-8'))
NOW = '2026-10-29T10:00:00+09:00'


class AMBriefingTests(unittest.TestCase):
    def setUp(self):
        self.doc = deepcopy(FIXTURE)

    def analyze(self):
        return b.analyze(self.doc, generated_at=NOW)

    def candidate(self, report, code='000001'):
        return next(c for c in report['candidates'] if c['stock_code'] == code)

    def test_three_sectors_three_leaders_and_same_engine(self):
        report = self.analyze()
        self.assertEqual([s['name'] for s in report['sectors']], ['반도체', '원전', '조선'])
        self.assertEqual([len(s['leaders']) for s in report['sectors']], [3, 3, 3])
        self.assertEqual(report['analysis'], am.analyze(self.doc, generated_at=NOW))
        self.assertFalse(report['candidate_confirmed'])
        self.assertFalse(report['d0_storage_allowed'])
        self.assertFalse(report['policy']['weighted_score'])

    def test_siri_message_has_verified_numbers_and_no_diagnostics(self):
        text = b.format_telegram(self.analyze())
        for fragment in ['9시 50분 기준', '합성 테스트 데이터', '모의 환경', '관측 기반 관심 후보',
                         '1위 반도체', '3등 가상조선3호', '상승 7.0%', '누적 거래대금 1,800억', '브리핑 종료']:
            self.assertIn(fragment, text)
        for fragment in ['candidate_confirmed', 'top5_', 'unverified', '|', '❗']:
            self.assertNotIn(fragment, text)
        self.assertLess(len(text), 1500)

    def test_missing_slot_holds_all_ranks(self):
        self.doc['rankings'].pop(1)
        report = self.analyze()
        self.assertEqual(report['sectors'], [])
        self.assertIn('09:30', report['completeness']['missing_slots'])
        self.assertIn('9시 30분', b.format_telegram(report).lstrip('0'))
        self.assertIn('보류', b.format_telegram(report))

    def test_early_preliminary_cutoff_never_claims_final_ranks(self):
        self.doc['selection_cutoff_at'] = '2026-10-29T09:40:00+09:00'
        report = self.analyze()
        self.assertEqual(report['sectors'], [])
        self.assertIn('9시 40분 기준', b.format_telegram(report))

    def test_duplicate_stock_rejects_instead_of_inflating_counts(self):
        self.doc['rankings'][0]['stocks'].append(deepcopy(self.doc['rankings'][0]['stocks'][0]))
        with self.assertRaisesRegex(ValueError, 'ambiguous_ranking_rows'):
            self.analyze()

    def test_duplicate_slot_rejects(self):
        self.doc['rankings'].append(deepcopy(self.doc['rankings'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate_slot'):
            self.analyze()

    def test_repeated_sector_tag_is_not_double_counted(self):
        before = self.analyze()
        self.doc['securities']['000001']['sectors'] *= 2
        after = self.analyze()
        self.assertEqual(before['sectors'], after['sectors'])

    def test_multi_sector_candidate_held_not_double_counted(self):
        tag = deepcopy(self.doc['securities']['000001']['sectors'][0]); tag['name'] = '원전'
        self.doc['securities']['000001']['sectors'].append(tag)
        report = self.analyze()
        self.assertEqual(self.candidate(report)['status'], 'held')
        self.assertEqual(next(s for s in report['sectors'] if s['name'] == '반도체')['evidence']['distinct_stocks'], 2)
        codes = [c['stock_code'] for s in report['sectors'] for c in s['leaders']]
        self.assertEqual(len(codes), len(set(codes)))

    def test_missing_metrics_hold_candidate(self):
        for kind, reason in [('turnover', 'intraday_turnover_unverified'), ('market_cap', 'market_cap_unverified'),
                             ('change_pct', 'change_pct_unverified')]:
            with self.subTest(kind=kind):
                doc = deepcopy(self.doc); doc['securities']['000001'][kind] = []
                candidate = self.candidate(b.analyze(doc, generated_at=NOW))
                self.assertEqual(candidate['status'], 'held')
                self.assertIn(reason, candidate['missing_evidence'])

    def test_unverified_metrics_are_not_displayed(self):
        self.doc['securities']['000001']['change_pct'][0]['verified'] = False
        report = self.analyze()
        self.assertIsNone(self.candidate(report)['metrics']['change_pct'])
        self.assertNotIn('가상반도체1호', b.format_telegram(report))

    def test_market_cap_strictly_above_3000_eok(self):
        for amount, status in [('299999999999', 'held'), ('300000000000', 'held'), ('300000000001', 'priority')]:
            self.doc['securities']['000001']['market_cap'][0]['value'] = amount
            self.assertEqual(self.candidate(self.analyze())['status'], status)

    def test_intraday_turnover_has_no_absolute_floor(self):
        for amount, status in [('0', 'priority'), ('100000000', 'priority'), ('49999999999', 'priority'),
                               ('50000000000', 'priority'), ('99999999999', 'priority'), ('100000000000', 'priority')]:
            self.doc['securities']['000001']['turnover'][-1]['value'] = amount
            self.assertEqual(self.candidate(self.analyze())['status'], status)

    def test_small_turnover_does_not_make_preliminary_sector(self):
        for code in ['000001', '000002', '000003']:
            self.doc['securities'][code]['turnover'][-1]['value'] = '75000000000'
        report = self.analyze()
        self.assertEqual(next(s for s in report['sectors'] if s['name'] == '반도체')['status'], 'priority')
        self.assertNotIn('예비 순위', b.format_telegram(report))
        self.assertNotIn('예비 후보', b.format_telegram(report))

    def test_turnover_relative_rank_and_measured_growth(self):
        report = self.analyze()
        comparison = self.candidate(report)['turnover_comparison']
        self.assertEqual(comparison['relative_rank'], 1)
        self.assertEqual(comparison['increase_won'], '90000000000')
        self.assertEqual(comparison['increase_per_second'], '50000000')
        self.assertEqual(comparison['growth_status'], '검증됨')
        self.doc['securities']['000001']['turnover'] = self.doc['securities']['000001']['turnover'][-1:]
        candidate = self.candidate(self.analyze())
        self.assertEqual(candidate['status'], 'priority')
        self.assertIsNone(candidate['turnover_comparison']['increase_per_second'])
        self.assertEqual(candidate['turnover_comparison']['growth_status'], '미확인')

    def test_unverified_previous_turnover_never_creates_growth(self):
        for mutation in [{'verified': False}, {'period': 'daily_close'}, {'observed_at': '2026-10-28T09:20:00+09:00'},
                         {'value': '999999999999'}]:
            self.doc = deepcopy(FIXTURE)
            self.doc['securities']['000001']['turnover'][0].update(mutation)
            self.assertIsNone(self.candidate(self.analyze())['turnover_comparison']['increase_per_second'])

    def test_closing_or_unspecified_turnover_never_substitutes(self):
        for period in ['daily_close', None]:
            self.doc['securities']['000001']['turnover'][-1]['period'] = period
            self.assertEqual(self.candidate(self.analyze())['status'], 'held')

    def test_previous_day_ranking_ignored(self):
        self.doc['rankings'][0]['observed_at'] = '2026-10-28T09:20:00+09:00'
        self.assertEqual(self.analyze()['sectors'], [])

    def test_old_future_late_or_stale_metrics_never_fill_missing(self):
        for observed, received in [('2026-10-28T09:50:00+09:00', '2026-10-28T09:50:00+09:00'),
                                    ('2026-10-29T09:51:00+09:00', '2026-10-29T09:51:00+09:00'),
                                    ('2026-10-29T09:50:00+09:00', '2026-10-29T09:50:01+09:00'),
                                    ('2026-10-29T09:40:00+09:00', '2026-10-29T09:40:00+09:00')]:
            metric = self.doc['securities']['000001']['change_pct'][0]
            metric.update(observed_at=observed, received_at=received)
            self.assertEqual(self.candidate(self.analyze())['status'], 'held')

    def test_mock_real_mixing_rejected_and_real_remains_synthetic(self):
        self.doc['rankings'][0]['environment'] = 'real'
        with self.assertRaises(ValueError): self.analyze()
        self.doc = deepcopy(FIXTURE)
        def real(value):
            if isinstance(value, dict):
                if 'environment' in value: value['environment'] = 'real'
                for v in value.values(): real(v)
            elif isinstance(value, list):
                for v in value: real(v)
        real(self.doc)
        text = b.format_telegram(self.analyze())
        self.assertIn('실전 환경 표기', text)
        self.assertIn('합성 테스트 데이터', text)

    def test_insufficient_companions_never_pad_three(self):
        self.doc['securities']['000001']['market_cap'] = []
        report = self.analyze()
        sector = next(s for s in report['sectors'] if s['name'] == '반도체')
        self.assertEqual(len(sector['leaders']), 2)
        self.doc['securities']['000002']['market_cap'] = []
        report = self.analyze()
        self.assertNotIn('반도체', [s['name'] for s in report['sectors']])
        self.assertIn('나머지 섹터 순위는 보류', b.format_telegram(report))

    def test_no_evidence_or_failed_snapshot_holds_sectors(self):
        self.doc['securities'] = {}
        self.assertEqual(self.analyze()['sectors'], [])
        self.doc = deepcopy(FIXTURE); self.doc['rankings'][0]['status'] = 'failed'
        self.assertEqual(self.analyze()['sectors'], [])

    def test_missing_name_and_exact_fractional_amount(self):
        self.doc['securities']['000001']['names'] = []
        self.doc['securities']['000001']['turnover'][-1]['value'] = '180012345678'
        text = b.format_telegram(self.analyze())
        self.assertIn('종목명 미확인 000001', text)
        self.assertIn('1,800.12345678억', text)

    def test_html_json_text_consistency_and_output_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(am, 'PROJECT', root), patch.object(am, 'REPORTS', root/'reports'):
                report = self.analyze(); paths = b.write_reports(report, 'synthetic')
                data = json.loads(paths[0].read_text(encoding='utf-8'))
                html = paths[1].read_text(encoding='utf-8'); text = paths[2].read_text(encoding='utf-8')
                embedded = re.search(r'id="analysis-data">(.*?)</script>', html, re.S).group(1)
                self.assertEqual(json.loads(embedded), data)
                self.assertEqual(unescape(re.search(r'id="telegram-text">(.*?)</pre>', html, re.S).group(1)), text)
                self.assertEqual(text, b.format_telegram(data))
                before = [p.read_bytes() for p in paths]
                with self.assertRaises(ValueError): b.write_reports(report, 'synthetic')
                self.assertEqual(before, [p.read_bytes() for p in paths])

    def test_tampering_even_with_recomputed_hash_rejected(self):
        report = self.analyze(); report['sectors'][0]['leaders'][0]['metrics']['change_pct']['value'] = '99'
        report['evaluation_reference'] = hashlib.sha256(am.canonical({k:v for k,v in report.items() if k!='evaluation_reference'}).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'briefing_analysis_mismatch'):
            b.format_telegram(report)

    def test_no_api_db_or_d0_during_analysis_and_cli(self):
        from kiwoom_collector import d0_store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root/'fixture.json'; fixture.write_text(json.dumps(self.doc), encoding='utf-8')
            with patch.object(am, 'PROJECT', root), patch.object(am, 'REPORTS', root/'reports'), \
                 patch('socket.socket.connect', side_effect=AssertionError('network')), \
                 patch('sqlite3.connect', side_effect=AssertionError('DB')), \
                 patch.object(d0_store, 'save_record', side_effect=AssertionError('D0')), \
                 patch.object(tg, 'deliver', side_effect=AssertionError('send')), \
                 patch.object(ot.OpenAITTS, 'synthesize', side_effect=AssertionError('TTS')), \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(b.main(['--fixture', str(fixture), '--name', 'dry-run']), 0)
                self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')

    def test_voice_and_tts_blocked_before_sessions_or_reservation_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root/'notification_state'; state.mkdir()
            old = state/'existing-voice.json'; old.write_text('{"status":"failed_or_unknown"}', encoding='utf-8')
            before = old.read_bytes()
            with patch.object(tg, 'PROJECT', root), patch.object(tg, 'STATE', state), \
                 patch.object(tg.requests, 'Session', side_effect=AssertionError('network')):
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, 'legacy_voice_disabled'):
                        tg.deliver(None, None, None, None, voice=(None, None), voice_only=True)
                    with self.assertRaisesRegex(ValueError, 'legacy_voice_disabled'):
                        ot.generate_once(None, None, 'new-attempt', approved=True)
                    with self.assertRaisesRegex(ValueError, 'legacy_voice_disabled'):
                        ot.OpenAITTS(None).synthesize(None)
                    for method in ['sendVoice', 'sendAudio']:
                        with self.assertRaisesRegex(ValueError, 'legacy_voice_disabled'):
                            tg.api_call(Mock(), None, method)
                self.assertEqual(old.read_bytes(), before)
                self.assertEqual(list(state.iterdir()), [old])

    def test_telegram_reuses_formatter_and_shared_duplicate_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(am, 'PROJECT', root), patch.object(am, 'REPORTS', root/'reports'), \
                 patch.object(tg, 'PROJECT', root), patch.object(tg, 'REPORTS', root/'reports'), \
                 patch.object(tg, 'STATE', root/'notification_state'):
                report = self.analyze(); paths = b.write_reports(report, 'review')
                parsed, text, html = tg.prepare(paths[0], paths[1])
                self.assertEqual(parsed, report)
                self.assertEqual(text, b.format_telegram(report))
                self.assertEqual(html, b.render(report))
                config = tg.Configuration('123456:'+'x'*30, '987654321', ('987654321',))
                session = Mock(); session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
                session.post.return_value = Mock(status_code=200, content=b'', json=Mock(return_value=dict(ok=True, result=dict(message_id=1, chat=dict(id=987654321, type='private')))))
                factory = Mock(return_value=session)
                first = tg.deliver(report, text, html, config, session_factory=factory)
                self.assertEqual(first['status'], 'sent')
                self.assertEqual(session.post.call_count, 2)
                session.post.assert_any_call('https://api.telegram.org/bot'+config.token+'/sendMessage',
                                             timeout=(5,20), allow_redirects=False, verify=True,
                                             json=dict(chat_id=config.chat_id, text=text))
                self.assertEqual(tg.deliver(report, text, html, config, session_factory=factory)['status'], 'duplicate_blocked')
                legacy = report['analysis']
                self.assertEqual(tg.deliver(legacy, tg.summary(legacy), None, config, session_factory=factory)['status'], 'duplicate_blocked')
                self.assertEqual(factory.call_count, 1)
                # Ambiguous old reservations also block the new text formatter.
                state_file = next((root/'notification_state').glob('*.json'))
                state_file.write_text('{"status":"failed_or_unknown"}', encoding='utf-8')
                before = state_file.read_bytes()
                self.assertEqual(tg.deliver(report, text, html, config, session_factory=factory)['status'], 'duplicate_blocked')
                self.assertEqual(state_file.read_bytes(), before)

    def test_telegram_cli_is_dry_run_and_rejects_inconsistent_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(am, 'PROJECT', root), patch.object(am, 'REPORTS', root/'reports'), \
                 patch.object(tg, 'PROJECT', root), patch.object(tg, 'REPORTS', root/'reports'), \
                 patch.object(tg, 'STATE', root/'notification_state'), \
                 patch.object(tg.requests, 'Session', side_effect=AssertionError('HTTP')):
                paths = b.write_reports(self.analyze(), 'review')
                with patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(tg.main(['--report', str(paths[0]), '--html', str(paths[1])]), 0)
                    self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')
                self.assertFalse(tg.STATE.exists())
                paths[1].write_text('<html>different report</html>', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'html_analysis_mismatch'):
                    tg.prepare(paths[0], paths[1])

    def test_new_metric_scope_and_duplicate_observation_are_safe(self):
        self.doc['securities']['000001']['change_pct'][0]['environment'] = 'real'
        self.assertEqual(self.candidate(self.analyze())['status'], 'held')
        self.doc = deepcopy(FIXTURE)
        self.doc['securities']['000001']['change_pct'] *= 2
        with self.assertRaisesRegex(ValueError, 'ambiguous_briefing_metric'):
            self.analyze()
