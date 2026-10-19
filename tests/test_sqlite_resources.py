"""Exercise real handles while retaining references: cleanup must not depend on GC."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.storage import Store


class SQLiteResourceTests(TestCase):
    def exercise_migration(self, fail):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.db'
            at = datetime(2026, 10, 19, 9, tzinfo=KST)
            with closing(Store(path)) as store:
                identifier = store.begin(at, 'mock', '1')
                store.finish(identifier, Snapshot(at, [{'bigd_rank': '1', 'stk_cd': '005930', 'stk_nm': '삼성전자'}]))
            with closing(sqlite3.connect(path)) as db, db:
                for table in ('stock_sector_events', 'stock_sectors', 'sectors', 'stocks', 'schema_migrations'):
                    db.execute(f'DROP TABLE {table}')
            connections, cursors = [], []
            real_connect = sqlite3.connect

            class TrackedCursor(sqlite3.Cursor):
                released = False

                def close(self):
                    super().close()
                    self.released = True

            class TrackedConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql.startswith('SELECT r.stk_cd,r.stk_nm'):
                        cursor = self.cursor(factory=TrackedCursor)
                        cursors.append(cursor)  # Deliberately keep the handle alive.
                        return cursor.execute(sql, parameters)
                    return super().execute(sql, parameters)

            def connect(*args, **kwargs):
                connection = real_connect(*args, **kwargs, factory=TrackedConnection)
                connections.append(connection)  # Keep connection alive after migration/exception.
                return connection

            try:
                with patch('kiwoom_collector.storage.sqlite3.connect', side_effect=connect):
                    if fail:
                        with patch('kiwoom_collector.sectors.upsert_stock', side_effect=sqlite3.OperationalError('injected backfill failure')):
                            with self.assertRaises(sqlite3.OperationalError):
                                Store(path)
                    else:
                        with closing(Store(path)) as migrated:
                            self.assertEqual(migrated.status()['records'], 1)
                self.assertEqual(len(connections), 1)
                self.assertEqual(len(cursors), 1)
                for cursor in cursors:
                    self.assertTrue(cursor.released)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        cursor.fetchone()
                for connection in connections:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute('SELECT 1')
                # Verify original history, and that a failed migration can be retried.
                with closing(Store(path)) as retried:
                    self.assertEqual(retried.status()['records'], 1)
                    self.assertEqual(retried.db.execute('SELECT stock_code FROM stocks').fetchone()[0], '005930')
                # Real rename/unlink before references are released: Windows must allow it.
                renamed = path.with_name('released.db')
                path.rename(renamed)
                renamed.unlink()
            finally:
                # Always release test-owned handles, including on a regression assertion failure.
                for cursor in cursors:
                    if not cursor.released:
                        cursor.close()
                for connection in connections:
                    connection.close()

    def test_successful_migration_releases_cursor_and_connection_before_file_removal(self):
        self.exercise_migration(fail=False)

    def test_failed_migration_releases_cursor_and_connection_before_file_removal(self):
        self.exercise_migration(fail=True)
