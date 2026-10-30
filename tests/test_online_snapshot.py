"""Synthetic files only: live WAL readers, consistency and bounded failure paths."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from kiwoom_collector import online_snapshot as s
from kiwoom_collector.cross_adapter import readonly_database


def stalled_worker(*args):
    time.sleep(30)  # terminated by the diagnostic watchdog, never an operating writer


class OnlineSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root/'source.db'
        self.output = self.root/'diagnostics'
        self.patch = patch.object(s, 'OUTPUT_ROOT', self.output)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def wal_writer(self):
        writer = sqlite3.connect(self.source, timeout=0.1)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY, version INTEGER, payload TEXT)')
        writer.execute("INSERT INTO observations VALUES(1,1,'baseline')")
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        writer.execute("INSERT INTO observations VALUES(2,1,'wal commit')")
        writer.commit()
        return writer

    def hashes(self):
        return {suffix: hashlib.sha256(Path(str(self.source)+suffix).read_bytes()).hexdigest()
                for suffix in ('', '-wal')}

    def test_live_wal_backup_preserves_latest_commit_and_source_content(self):
        writer = self.wal_writer()
        before = self.hashes()
        result = s.capture_snapshot(self.source, name='live', seconds=5)
        self.assertTrue(result['success'], result)
        self.assertTrue(result['source_connection_released'])
        self.assertEqual(before, self.hashes())
        # SHM read marks are SQLite coordination, not promised byte-invariant.
        final = Path(result['snapshot_path'])
        with readonly_database(final) as (db, provenance):
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
        self.assertTrue(provenance['hash_unchanged'])
        self.assertEqual(result['sha256'], provenance['sha256'])
        self.assertEqual(result['quick_check'], 'ok')
        self.assertGreater(result['snapshot_size_bytes'], 0)
        self.assertLess(result['source_closed_at'], result['finished_at'])
        self.assertEqual(json.loads((final.parent/'manifest.json').read_text())['status'], 'success')
        writer.execute("INSERT INTO observations VALUES(3,1,'still writable')")
        writer.commit()

    def test_writer_changes_during_paged_backup_without_torn_snapshot(self):
        writer = self.wal_writer()
        writer.executemany('INSERT INTO observations VALUES(?,1,?)',
                           [(i, 'x'*5000) for i in range(3, 100)])
        writer.commit()
        changed = []

        def concurrent_commit(status, remaining, total):
            if remaining and not changed:
                writer.execute('UPDATE observations SET version=2')
                writer.commit()
                changed.append(True)

        dest = self.root/'concurrent.db'
        s._backup_once(str(self.source), str(dest), 5, 1, progress_hook=concurrent_commit)
        self.assertTrue(changed)
        with closing(sqlite3.connect(dest)) as db:
            self.assertEqual(db.execute('PRAGMA quick_check').fetchall(), [('ok',)])
            self.assertEqual(db.execute('SELECT count(DISTINCT version) FROM observations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 99)
        self.assertEqual(writer.execute('SELECT min(version) FROM observations').fetchone()[0], 2)

    def test_immutable_is_not_used_and_source_sql_write_is_denied(self):
        self.wal_writer()
        with closing(s._source_connection(self.source)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
            for sql in ('INSERT INTO observations VALUES(3,1,\'bad\')',
                        'PRAGMA journal_mode=DELETE', 'ATTACH DATABASE ":memory:" AS extra'):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(sql)

    def test_locked_rollback_source_fails_without_publication_and_releases_handles(self):
        writer = sqlite3.connect(self.source, timeout=0.1)
        self.addCleanup(writer.close)
        writer.execute('CREATE TABLE t(x)')
        writer.commit()
        writer.execute('BEGIN EXCLUSIVE')
        result = s.capture_snapshot(self.source, name='locked', seconds=5)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'source_locked', result)
        self.assertEqual(result['sqlite_errorcode'], sqlite3.SQLITE_BUSY)
        self.assertTrue(result['source_connection_released'])
        self.assertEqual([p.name for p in (self.output/'locked').iterdir()], ['manifest.json'])
        writer.rollback()
        writer.execute('INSERT INTO t VALUES(1)')
        writer.commit()

    def test_callback_deadline_aborts_without_success(self):
        writer = self.wal_writer()
        with patch.object(s.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(s.SnapshotAbort, 'online_deadline_exceeded'):
                s._backup_once(str(self.source), str(self.root/'deadline.db'), 1, 64)
        writer.close()
        self.source.rename(self.root/'released.db')

    def test_parent_watchdog_kills_only_its_owned_child_and_audits_failure(self):
        self.wal_writer()
        before = self.hashes()
        with patch.object(s, '_worker', stalled_worker):
            start = time.monotonic()
            result = s.capture_snapshot(self.source, name='watchdog', seconds=0.2)
        self.assertLess(time.monotonic()-start, 4)
        self.assertEqual(result['reason'], 'online_watchdog_timeout')
        self.assertFalse(result['success'])
        self.assertTrue(result['source_connection_released'])
        self.assertEqual(self.hashes(), before)
        self.assertFalse(Path(result['snapshot_path']).exists())

    def test_interrupted_parent_records_cancelled_and_terminates_owned_worker(self):
        self.wal_writer()
        # Inject a console interruption without adding unpicklable methods to a
        # Windows spawn Process. Actual termination is covered by watchdog test.
        context, process = Mock(), Mock()
        context.Pipe.return_value = (Mock(), Mock())
        context.Process.return_value = process
        process.join.side_effect = [KeyboardInterrupt(), None]
        process.is_alive.side_effect = [True, False, False, False]
        process.exitcode = -15
        with patch.object(s.multiprocessing, 'get_context', return_value=context):
            result = s.capture_snapshot(self.source, name='interrupted')
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['success'])
        self.assertTrue(result['source_connection_released'])
        self.assertFalse(Path(result['snapshot_path']).exists())
        process.terminate.assert_called_once()

    def test_bad_database_does_not_publish_or_change_original(self):
        self.source.write_bytes(b'not a database')
        before = self.source.read_bytes()
        result = s.capture_snapshot(self.source, name='invalid')
        self.assertFalse(result['success'])
        self.assertIn('sqlite_errorcode', result)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual([p.name for p in (self.output/'invalid').iterdir()], ['manifest.json'])

    def test_validation_failure_never_publishes_partial(self):
        self.wal_writer()
        with patch.object(s, '_validate_destination', side_effect=s.SnapshotAbort('destination_integrity_failed')):
            result = s.capture_snapshot(self.source, name='validation')
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'destination_integrity_failed')
        self.assertEqual([p.name for p in (self.output/'validation').iterdir()], ['manifest.json'])

    def test_existing_output_and_invalid_scope_are_preserved(self):
        self.wal_writer()
        self.output.mkdir()
        run = self.output/'existing'
        run.mkdir()
        (run/'manifest.json').write_text('preserve')
        with self.assertRaises(FileExistsError):
            s.capture_snapshot(self.source, name='existing')
        self.assertEqual((run/'manifest.json').read_text(), 'preserve')
        for kwargs in ({'name':'../escape'}, {'name':'bad', 'seconds':float('nan')},
                       {'name':'bad', 'seconds':31}, {'name':'bad', 'pages':0}):
            with self.assertRaises(ValueError):
                s.capture_snapshot(self.source, **kwargs)
        self.assertEqual(list(self.output.iterdir()), [run])

    def test_missing_source_never_created(self):
        with self.assertRaises(FileNotFoundError):
            s.capture_snapshot(self.source, name='missing')
        self.assertFalse(self.source.exists())
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing modes')
    def test_windows_sharing_conflict_preserves_error_32(self):
        import ctypes
        from ctypes import wintypes
        self.source.write_bytes(b'fixture')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                           wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        handle = create(str(self.source), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            result = s.capture_snapshot(self.source, name='sharing')
            self.assertEqual(result['winerror'], 32)
            self.assertEqual(result['reason'], 'sharing_violation')
            self.assertFalse(result['success'])
        finally:
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle(handle)

    def test_snapshot_flows_into_unchanged_c8_without_fabricating_evidence(self):
        import test_am_input_adapter as fixture
        from kiwoom_collector import am_data_diagnostic as diagnostic
        original = fixture.AMInputAdapterTests('test_no_manifest_never_promotes_start_time_or_numeric_units')
        original.setUp()
        self.addCleanup(original.doCleanups)
        writer = sqlite3.connect(original.db)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE collections SET record_count=20")
        writer.commit()
        result = s.capture_snapshot(original.db, name='c8')
        self.assertTrue(result['success'], result)
        report = diagnostic.diagnose(Path(result['snapshot_path']), trade_date='2026-10-29',
            cutoff_at='2026-10-29T09:50:00+09:00', environment='mock', synthetic=True)
        self.assertEqual(sum(row['stored_top20_complete'] for row in report['ranking_slots']), 4)
        self.assertEqual(sum(row['verified_top20_complete'] for row in report['ranking_slots']), 0)
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])


if __name__ == '__main__':
    unittest.main()
