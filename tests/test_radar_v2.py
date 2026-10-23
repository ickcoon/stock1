"""Offline Radar v2 comparisons and conservative turnover aggregation."""
from contextlib import closing
from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.client import Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.quotes import migrate_quotes, SPEC_VERSION
from kiwoom_collector.radar import radar_report, render_radar, export_radar


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.text = []
        self.depths = []

    def handle_starttag(self, tag, attrs):
        if tag in ('details', 'style', 'script'):
            self.hidden += 1
            self.depths.append(tag)

    def handle_endtag(self, tag):
        if tag in ('details', 'style', 'script'):
            self.hidden -= 1

    def handle_data(self, text):
        if not self.hidden:
            self.text.append(text)


class RadarV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'radar.db'
        self.output = Path(self.tmp.name)/'radar.html'
        with closing(Store(self.path)) as store:
            migrate_quotes(store.db)
            store.db.commit()

    def run_at(self, at='2026-10-20T10:00:00+09:00', codes=(1, 2, 3), environment='mock', qry='1', mode=1, status='success'):
        at = datetime.fromisoformat(at)
        with closing(Store(self.path)) as store:
            identifier = store.begin(at, environment, qry, test_mode=bool(mode))
            if status == 'success':
                store.finish(identifier, Snapshot(at, [dict(bigd_rank=str(i), stk_cd=f'{code:06}', stk_nm=f'종목 {code}')
                                                       for i, code in enumerate(codes, 1)]))
            elif status != 'running':
                store.fail(identifier, 'synthetic')
        return identifier

    def tag(self, sector, *codes):
        with closing(Store(self.path)) as store:
            manager = SectorManager(store)
            manager.add(sector)
            for code in codes:
                manager.tag(f'{code:06}', sector)

    def batch(self, run, *, values=None, unit=True, at=None, status='success', batch_id='batch', api='ka10095', mode=1):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            parent = db.execute('SELECT * FROM collections WHERE id=?', (run,)).fetchone()
            at = at or parent['collected_at']
            db.execute('''INSERT INTO quote_batches(id,collection_id,environment,spec_version,started_at,status,api_id,test_mode)
                VALUES(?,?,?,?,?,?,?,?)''', (batch_id, run, parent['environment'], SPEC_VERSION, at, status, api, mode))
            rows = db.execute('SELECT * FROM ranking_records WHERE collection_id=? ORDER BY rank', (run,)).fetchall()
            for row in rows:
                code = int(row['stk_cd'])
                if values is not None and code not in values:
                    continue
                amount, rate, state = values[code] if values else ('100000000', '2', 'success')
                observed = datetime.fromisoformat(at)
                norm = dict(version=1, change_percent=rate, price_status='unverified_sign_semantics',
                            turnover_won=amount, turnover_source_unit='million_KRW',
                            api_date_raw=observed.strftime('%Y%m%d'), api_time_raw=observed.strftime('%H%M%S'))
                if unit:
                    norm.update(turnover_unit_status='verified', turnover_unit_evidence='synthetic fixture only')
                db.execute('''INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,api_id,status,
                    request_finished_at,normalized_json,test_mode,raw_json,issues_json) VALUES(?,?,?,?,?,?,?,?,?,'{}','[]')''',
                    (batch_id, run, row['rank'], row['stk_cd'], api, state, at, json.dumps(norm), mode))
            db.commit()

    def report(self, environment='mock'):
        return radar_report(self.path, '2026-10-20', environment)

    def current(self, environment='mock'):
        return self.report(environment)['overview']['current']

    def test_interest_sort_count_then_top5_top10(self):
        self.run_at(codes=tuple(range(1, 21)))
        self.tag('A', 11, 12, 13)
        self.tag('B', 1, 2)
        self.tag('C', 3, 6)
        self.tag('D', 7, 11)
        sectors = [g for g in self.current()['sectors'] if g['name'] != '미분류']
        self.assertEqual([g['name'] for g in sectors], ['A', 'B', 'C', 'D'])
        self.assertEqual([(g['interest_count'], g['top5'], g['top10']) for g in sectors],
                         [(3, 0, 0), (2, 2, 2), (2, 1, 2), (2, 0, 1)])

    def test_previous_delta_entrants_and_emerging(self):
        self.run_at('2026-10-20T09:40:00+09:00', codes=(1, 2))
        current = self.run_at(codes=(1, 3, 4))
        self.tag('반도체', 1, 3, 4)
        self.tag('전기', 2)
        self.batch(current)
        view = self.report()['overview']
        self.assertEqual([s['stk_cd'] for s in view['entrants']], ['000003', '000004'])
        self.assertEqual(view['emerging'][0]['count_delta'], 2)
        self.assertEqual(view['current']['sectors'][0]['count_delta'], 2)

    def test_first_run_does_not_invent_new_entries(self):
        self.run_at()
        view = self.report()['overview']
        self.assertIsNone(view['previous'])
        self.assertIsNone(view['entrants'])
        self.assertTrue(all(g['count_delta'] is None for g in view['current']['sectors']))
        self.assertIn('비교 대상 없음', render_radar(self.report()))

    def test_comparison_excludes_environment_query_mode_date_and_failed_runs(self):
        expected = self.run_at('2026-10-20T09:00:00+09:00')
        self.run_at('2026-10-20T09:50:00+09:00', environment='real')
        self.run_at('2026-10-20T09:51:00+09:00', qry='2')
        self.run_at('2026-10-20T09:52:00+09:00', mode=0)
        self.run_at('2026-10-19T09:53:00+09:00')
        self.run_at('2026-10-20T09:54:00+09:00', status='error')
        self.run_at('2026-10-20T10:20:00+09:00', status='running')
        newest = self.run_at()
        view = self.report()['overview']
        self.assertEqual(view['current']['collection']['id'], newest)
        self.assertEqual(view['previous']['collection']['id'], expected)

    def test_actual_time_order_handles_offsets_not_lexical_strings(self):
        old = self.run_at('2026-10-20T09:40:00+09:00')
        new = self.run_at()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE collections SET collected_at=? WHERE id=?', ('2026-10-20T01:00:00+00:00', new))
            db.commit()
        view = self.report()['overview']
        self.assertEqual(view['current']['collection']['id'], new)
        self.assertEqual(view['previous']['collection']['id'], old)

    def test_multisector_and_unclassified_unique_market_total(self):
        run = self.run_at()
        self.tag('A', 1, 2)
        self.tag('B', 1)
        self.batch(run)
        current = self.current()
        self.assertEqual(current['market']['unique_count'], 3)
        self.assertEqual(current['market']['turnover_won'], '300000000')
        self.assertEqual(sum(g['interest_count'] for g in current['sectors']), 4)
        self.assertEqual(next(g for g in current['sectors'] if g['name'] == 'A')['turnover_won'], '200000000')
        self.assertEqual(next(g for g in current['sectors'] if g['name'] == '미분류')['interest_count'], 1)
        self.assertNotIn('미분류', [g['name'] for g in self.report()['overview']['leaders']])

    def test_verified_cash_drives_sort_and_mean_without_weighted_score(self):
        run = self.run_at()
        self.tag('A', 1, 2)
        self.tag('B', 3)
        self.batch(run, values={1: ('100', '1', 'success'), 2: ('300', '-1', 'success'), 3: ('1000', '0', 'success')})
        current = self.current()
        self.assertEqual(current['ordering'], 'turnover')
        self.assertEqual([g['name'] for g in current['sectors']], ['B', 'A'])
        a = current['sectors'][1]
        self.assertEqual((a['turnover_won'], a['average_turnover_won'], a['up'], a['down'], a['flat']), ('400', '200', 1, 1, 0))
        self.assertFalse(current['turnover_ranking_confirmed'])  # mock is never real evidence

    def test_real_confirmation_requires_full_verified_coverage(self):
        run = self.run_at(environment='real')
        self.batch(run)
        self.assertTrue(self.current('real')['turnover_ranking_confirmed'])

    def test_source_unit_declaration_alone_never_verifies_amount(self):
        run = self.run_at()
        self.batch(run, unit=False)
        current = self.current()
        self.assertEqual(current['ordering'], 'interest')
        self.assertIsNone(current['market']['turnover_won'])
        self.assertEqual(current['market']['turnover_count'], 0)
        self.assertIn('거래대금 기준 순위 확정 보류', render_radar(self.report()))

    def test_partial_coverage_uses_known_sum_not_zero_or_full_mean(self):
        run = self.run_at()
        self.tag('A', 1, 2, 3)
        self.batch(run, values={1: ('200', '1', 'partial'), 2: (None, None, 'partial')}, status='partial')
        group = self.current()['sectors'][0]
        self.assertEqual((group['turnover_won'], group['average_turnover_won'], group['turnover_count']), ('200', '200', 1))
        self.assertAlmostEqual(group['coverage_percent'], 100/3)
        self.assertEqual((group['up'], group['valid_count'], group['excluded']), (1, 1, 2))
        self.assertEqual(self.current()['ordering'], 'interest')

    def test_failed_and_running_batch_do_not_supply_turnover(self):
        for index, status in enumerate(('running', 'error', 'interrupted')):
            with self.subTest(status=status):
                run = self.run_at(at=f'2026-10-20T10:0{index}:00+09:00')
                self.batch(run, status=status, batch_id=status)
                self.assertIsNone(self.current()['market']['turnover_won'])

    def test_failed_observation_not_counted_as_success(self):
        run = self.run_at()
        self.batch(run, values={1: ('100', '2', 'error'), 2: ('100', '-1', 'success'), 3: ('100', '0', 'success')})
        group = self.current()['sectors'][0]
        self.assertEqual((group['up'], group['down'], group['flat'], group['excluded']), (0, 1, 1, 1))
        self.assertEqual(group['turnover_count'], 2)

    def test_test_observation_on_operating_parent_is_excluded_from_v2(self):
        run = self.run_at(mode=0)
        self.batch(run, mode=1)
        current = self.current()
        self.assertIsNone(current['batch_id'])
        self.assertEqual(current['sectors'][0]['valid_count'], 0)
        self.assertIsNone(current['market']['turnover_won'])
        self.assertEqual(len(current['stocks'][0]['observations']), 1)  # retained diagnostic evidence

    def test_batch_mode_mismatch_is_excluded(self):
        run = self.run_at()
        self.batch(run)
        # Simulate a corrupt legacy batch without modifying the observation mode.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA ignore_check_constraints=ON')
            db.execute('UPDATE quote_batches SET test_mode=0')
            db.commit()
        self.assertEqual(self.current()['sectors'][0]['valid_count'], 0)

    def test_wrong_environment_batch_cannot_join(self):
        run = self.run_at()
        self.batch(run)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE quote_batches SET environment='real'")
            db.commit()
        self.assertEqual(self.current()['market']['turnover_count'], 0)

    def test_latest_batch_never_backfills_missing_targets_from_old_batch(self):
        run = self.run_at()
        self.batch(run, batch_id='older')
        # Old primary key permits a new observation for a previously missing code only.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DELETE FROM quote_observations WHERE requested_code='000001'")
            db.commit()
        self.batch(run, at='2026-10-20T10:01:00+09:00', batch_id='newer', values={1: ('500', '2', 'success')})
        current = self.current()
        self.assertEqual(current['batch_id'], 'newer')
        self.assertEqual((current['market']['turnover_count'], current['market']['turnover_won']), (1, '500'))
        self.assertEqual(current['sectors'][0]['valid_count'], 1)

    def test_empty_new_batch_does_not_reuse_older_batch(self):
        run = self.run_at()
        self.batch(run, batch_id='older')
        self.batch(run, batch_id='empty', at='2026-10-20T10:01:00+09:00', values={99: ('1', '1', 'success')})
        current = self.current()
        self.assertEqual(current['batch_id'], 'empty')
        self.assertEqual(current['market']['turnover_count'], 0)
        self.assertEqual(current['sectors'][0]['valid_count'], 0)

    def test_tied_batch_timestamps_use_storage_order_not_uuid(self):
        run = self.run_at()
        self.batch(run, batch_id='z-older')
        self.batch(run, batch_id='a-later-empty', values={99: ('1', '1', 'success')})
        current = self.current()
        self.assertEqual(current['batch_id'], 'a-later-empty')
        self.assertEqual(current['market']['turnover_count'], 0)

    def test_different_time_cumulative_values_never_combined(self):
        run = self.run_at()
        self.batch(run)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT normalized_json FROM quote_observations WHERE requested_code='000001'").fetchone()
            norm = json.loads(row[0]); norm['api_time_raw'] = '100459'
            db.execute("UPDATE quote_observations SET normalized_json=?,request_finished_at='2026-10-20T10:04:59+09:00' WHERE requested_code='000001'", (json.dumps(norm),))
            row = db.execute("SELECT normalized_json FROM quote_observations WHERE requested_code='000002'").fetchone()
            norm = json.loads(row[0]); norm['api_time_raw'] = '095959'
            db.execute("UPDATE quote_observations SET normalized_json=?,request_finished_at='2026-10-20T09:59:59+09:00' WHERE requested_code='000002'", (json.dumps(norm),))
            db.commit()
        self.assertTrue(self.current()['batch_coherent'])  # exactly 300s
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE quote_observations SET request_finished_at='2026-10-20T09:59:58+09:00' WHERE requested_code='000002'")
            db.commit()
        self.assertFalse(self.current()['batch_coherent'])
        self.assertIsNone(self.current()['market']['turnover_won'])

    def test_historical_quote_never_fills_current_missing_quote(self):
        old = self.run_at('2026-10-20T09:40:00+09:00')
        self.batch(old)
        self.run_at()
        self.assertIsNone(self.current()['market']['turnover_won'])
        self.assertEqual(self.current()['sectors'][0]['up'], 0)

    def test_wrong_date_or_stale_api_time_excluded_even_with_verified_unit(self):
        run = self.run_at()
        self.batch(run, at='2026-10-22T10:00:00+09:00')
        self.assertEqual(self.current()['market']['turnover_count'], 0)
        self.assertEqual(self.current()['sectors'][0]['valid_count'], 0)

    def test_ka10007_only_is_not_a_turnover_source(self):
        run = self.run_at()
        self.batch(run, api='ka10007')
        self.assertEqual(self.current()['market']['turnover_count'], 0)
        self.assertEqual(self.current()['sectors'][0]['up'], 3)

    def test_zero_is_valid_but_nan_negative_and_missing_are_not(self):
        run = self.run_at()
        self.batch(run, values={1: ('0', '0', 'success'), 2: ('NaN', '2', 'partial'), 3: ('-1', '2', 'partial')})
        current = self.current()
        self.assertEqual(current['market']['turnover_won'], '0')
        self.assertEqual(current['market']['turnover_count'], 1)
        self.assertEqual(current['ordering'], 'interest')

    def test_default_screen_hides_uuid_raw_json_and_diagnostics(self):
        run = self.run_at()
        self.batch(run)
        html = render_radar(self.report())
        visible = VisibleText(); visible.feed(html)
        text = ''.join(visible.text)
        self.assertNotIn(run, text)
        self.assertNotIn('normalized_json', text)
        self.assertNotIn('API 시세 시각', text)
        self.assertNotIn('시점 일치:', text)
        self.assertIn(run, html)
        self.assertIn('전체 종목', html)
        self.assertIn('v1 상세 진단', html)
        self.assertEqual(html.count('<html'), 1)
        self.assertEqual(html.count('<main>'), 1)
        self.assertNotIn('<script', html)
        self.assertNotIn('https://', html)

    def test_xss_escaped_in_sector_stock_and_diagnostic_json(self):
        run = self.run_at()
        self.tag('<img src=x onerror=alert(1)>', 1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE ranking_records SET stk_nm=? WHERE stk_cd=?', ('</summary><script>alert(1)</script>', '000001'))
            db.commit()
        html = render_radar(self.report())
        self.assertNotIn('<img ', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;img', html)
        self.assertIn('&lt;script&gt;', html)

    def test_export_readonly_and_existing_output_never_overwritten(self):
        self.run_at()
        before = self.path.read_bytes()
        with patch('socket.socket.connect', side_effect=AssertionError('offline')), \
             patch('kiwoom_collector.__main__.Store', side_effect=AssertionError('no Store')):
            export_radar(self.path, '2026-10-20', 'mock', self.output)
        saved = self.output.read_bytes()
        with self.assertRaises(ValueError):
            export_radar(self.path, '2026-10-20', 'mock', self.output)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.output.read_bytes(), saved)

    def test_legacy_schema_without_quotes_still_displays_interest(self):
        self.run_at()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DROP TABLE quote_observations')
            db.execute('DROP TABLE quote_batches')
            db.commit()
        self.assertEqual(self.current()['market']['unique_count'], 3)
        self.assertIsNone(self.current()['market']['turnover_won'])

    def test_no_collections_is_a_standalone_empty_report(self):
        report = self.report()
        self.assertIsNone(report['overview'])
        html = render_radar(report)
        self.assertIn('성공한 순위 수집이 없습니다', html)
        self.assertTrue(html.endswith('</html>'))

    def test_unidentified_code_cannot_create_false_full_turnover_coverage(self):
        run = self.run_at()
        self.batch(run)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE ranking_records SET stk_cd=NULL WHERE stk_cd='000001'")
            db.commit()
        current = self.current()
        self.assertEqual(current['market']['unique_count'], 2)
        self.assertEqual(current['market']['unidentified_count'], 1)
        self.assertEqual(current['ordering'], 'interest')

    def test_declared_verified_unit_without_evidence_or_correct_spec_is_rejected(self):
        run = self.run_at()
        self.batch(run)
        with closing(sqlite3.connect(self.path)) as db:
            for evidence in ('', ' ', None):
                rows = db.execute('SELECT rank,normalized_json FROM quote_observations').fetchall()
                for rank, text in rows:
                    norm = json.loads(text); norm['turnover_unit_evidence'] = evidence
                    db.execute('UPDATE quote_observations SET normalized_json=? WHERE rank=?', (json.dumps(norm), rank))
                db.commit()
                self.assertEqual(self.current()['market']['turnover_count'], 0)
            db.execute("UPDATE quote_batches SET spec_version='unverified'")
            db.commit()
        self.assertEqual(self.current()['market']['turnover_count'], 0)

    def test_new_entries_do_not_show_unverified_or_wrong_mode_rates(self):
        self.run_at('2026-10-20T09:40:00+09:00', codes=(1, 2))
        run = self.run_at(codes=(1, 3), mode=1)
        self.batch(run, values={1: ('100', '2', 'success'), 3: ('100', 'NaN', 'partial')})
        entry = self.report()['overview']['entrants'][0]
        self.assertEqual(entry['stk_cd'], '000003')
        self.assertIsNone(entry['valid_quote'])
        html = render_radar(self.report())
        visible = VisibleText(); visible.feed(html)
        self.assertNotIn('NaN%', ''.join(visible.text))

    def test_sensitive_unknown_raw_fields_are_not_added_to_html(self):
        run = self.run_at()
        self.batch(run)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE quote_observations SET raw_json=?', (json.dumps(dict(token='PRIVATE_TOKEN',
                account='PRIVATE_ACCOUNT', return_msg='PRIVATE_SERVER', cur_prc='PRIVATE_TEXT')),))
            db.commit()
        html = render_radar(self.report())
        for value in ('PRIVATE_TOKEN', 'PRIVATE_ACCOUNT', 'PRIVATE_SERVER', 'PRIVATE_TEXT'):
            self.assertNotIn(value, html)

    def test_unclassified_growth_is_not_a_leading_or_emerging_sector(self):
        self.run_at('2026-10-20T09:40:00+09:00', codes=(1,))
        self.run_at(codes=(1, 2, 3, 4))
        self.tag('A', 1)
        view = self.report()['overview']
        self.assertEqual([g['name'] for g in view['leaders']], ['A'])
        self.assertEqual(view['emerging'], [])
        self.assertEqual(view['current']['market']['unique_count'], 4)
        self.assertEqual(len(view['entrants']), 3)
        html = render_radar(self.report())
        self.assertNotIn("class='sector-name'>미분류", html)
        self.assertIn('미분류 3종목', html)
        self.assertIn('미분류 종목 목록 보기', html)

    def test_unclassified_list_is_hidden_and_keeps_original_codes_and_names(self):
        self.run_at()
        self.tag('A', 1)
        html = render_radar(self.report())
        section = html.split('<section class="unclassified">', 1)[1].split('</section>', 1)[0]
        self.assertIn('미분류 2종목', section)
        visible = VisibleText(); visible.feed(section)
        self.assertNotIn('종목 2', ''.join(visible.text))
        self.assertNotIn('000002', ''.join(visible.text))
        self.assertIn('종목 2 (000002)', section)
        self.assertIn('종목 3 (000003)', section)

    def test_no_valid_quote_is_unconfirmed_not_zero_over_zero(self):
        self.run_at()
        self.tag('A', 1, 2, 3)
        html = render_radar(self.report())
        visible = VisibleText(); visible.feed(html)
        text = ''.join(visible.text)
        self.assertIn('시세 미확인', text)
        self.assertNotIn('상승 0/0', text)
        self.assertNotIn('하락 0 · 보합 0', text)

    def test_partial_quote_coverage_shows_valid_and_total_counts(self):
        run = self.run_at()
        self.tag('A', 1, 2, 3)
        self.batch(run, values={1: ('100', '2', 'partial'), 2: (None, None, 'partial')}, status='partial')
        html = render_radar(self.report())
        visible = VisibleText(); visible.feed(html)
        self.assertIn('상승 1 / 유효 1 · 전체 3', ''.join(visible.text))
        self.assertIn('거래대금 일부 검증 1/3', ''.join(visible.text))

    def test_fully_valid_quotes_still_show_total_and_real_down_flat_counts(self):
        run = self.run_at()
        self.tag('A', 1, 2, 3)
        self.batch(run, values={1: ('100', '2', 'success'), 2: ('200', '-1', 'success'), 3: ('300', '0', 'success')})
        html = render_radar(self.report())
        visible = VisibleText(); visible.feed(html)
        text = ''.join(visible.text)
        self.assertIn('상승 1 / 유효 3 · 전체 3', text)
        self.assertIn('하락 1 · 보합 1', text)

    def test_top3_is_only_sector_count_and_turnover_verification_state(self):
        self.run_at()
        self.tag('A', 1, 2, 3)
        html = render_radar(self.report())
        top3 = html.split('<div class="kpis">', 1)[1].split('</ol>', 1)[0]
        self.assertIn('<b>A</b>', top3)
        self.assertIn('TOP20 3종목', top3)
        self.assertIn('거래대금 미검증', top3)
        self.assertNotIn('상승', top3)
        self.assertNotIn('종목 1', top3)
        self.assertIn("class='rep'>종목 1 · 종목 2 · 종목 3", html)

    def test_all_unclassified_keeps_market_count_with_no_sector_ranks(self):
        self.run_at('2026-10-20T09:40:00+09:00', codes=(1,))
        self.run_at()
        view = self.report()['overview']
        self.assertEqual(view['leaders'], [])
        self.assertEqual(view['emerging'], [])
        self.assertEqual(view['current']['market']['unique_count'], 3)
        self.assertEqual(len(view['entrants']), 2)
        html = render_radar(self.report())
        self.assertIn('분류된 섹터 없음', html)
        self.assertIn('미분류 3종목', html)
        self.assertIn('v1 상세 진단', html)

    def test_cash_unit_validation_and_unknown_quotes_are_independent(self):
        run = self.run_at(environment='real')
        self.tag('A', 1, 2, 3)
        self.batch(run, values={n: ('100', None, 'partial') for n in (1, 2, 3)})
        html = render_radar(self.report('real'))
        top3 = html.split('<div class="kpis">', 1)[1].split('</ol>', 1)[0]
        self.assertIn('거래대금 검증 완료', top3)
        visible = VisibleText(); visible.feed(html)
        self.assertIn('시세 미확인', ''.join(visible.text))
        self.assertNotIn('상승 0/0', ''.join(visible.text))
