"""Bounded online backup into a new diagnostic DB; never uses Store or transports.

Live access is deliberately separate from cross_adapter's offline policy.
mode=ro forbids database writes, but does NOT promise zero SHM/locking impact.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import sqlite3
import time

PROJECT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT / 'diagnostics' / 'online_snapshots'
KST = timezone(timedelta(hours=9))


def stamp():
    return datetime.now(KST).isoformat()


class SnapshotAbort(Exception):
    def __init__(self, reason, sqlite_status=None):
        self.reason = reason
        self.sqlite_status = sqlite_status
        super().__init__(reason)


def _error(error):
    result = dict(reason='snapshot_failed', error_type=type(error).__name__)
    if isinstance(error, SnapshotAbort):
        result['reason'] = error.reason
        if error.sqlite_status is not None:
            result.update(sqlite_errorcode=error.sqlite_status,
                          sqlite_errorname={sqlite3.SQLITE_BUSY:'SQLITE_BUSY',
                                            sqlite3.SQLITE_LOCKED:'SQLITE_LOCKED'}.get(error.sqlite_status))
    elif isinstance(error, sqlite3.Error):
        code = getattr(error, 'sqlite_errorcode', None)
        result.update(sqlite_errorcode=code, sqlite_errorname=getattr(error, 'sqlite_errorname', None))
        result['reason'] = ('source_locked' if isinstance(code, int) and code & 255 in
                            (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else 'sqlite_access_or_backup_failed')
    elif isinstance(error, OSError):
        result.update(errno=error.errno, winerror=getattr(error, 'winerror', None))
        result['reason'] = 'sharing_violation' if getattr(error, 'winerror', None) == 32 else 'file_access_failed'
    # Exception text can contain paths/server data; keep only numeric codes.
    return result


def _readable_shared(path):
    """Windows preflight preserves native error; never denies writers/deleters."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle(handle)


def _source_connection(path):
    connection = sqlite3.connect(Path(path).as_uri()+'?mode=ro&cache=private',
                                 uri=True, timeout=0.1, isolation_level=None)
    try:
        connection.execute('PRAGMA query_only=ON')
        allowed = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                   sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION)
        connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK
                                  if action in allowed else sqlite3.SQLITE_DENY)
        return connection
    except BaseException:
        connection.close()
        raise


def _backup_once(source, destination, seconds, pages, *, progress_hook=None):
    """One attempt, no outer retries; hook is only for synthetic concurrency tests."""
    deadline = time.monotonic()+seconds
    metadata = dict(source_open_started_at=stamp(), source_closed_at=None)
    src = dst = None
    try:
        _readable_shared(source)
        src = _source_connection(source)
        dst = sqlite3.connect(destination, timeout=0.1)

        def progress(status, remaining, total):
            if time.monotonic() >= deadline:
                raise SnapshotAbort('online_deadline_exceeded')
            if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise SnapshotAbort('source_locked', status)
            if progress_hook is not None:
                progress_hook(status, remaining, total)

        src.backup(dst, pages=pages, progress=progress, sleep=0.0)
        if time.monotonic() >= deadline:
            raise SnapshotAbort('online_deadline_exceeded')
        metadata['backup_completed_at'] = stamp()
    finally:
        # Close live source BEFORE destination validation or hashing.
        if src is not None:
            src.close()
        metadata['source_closed_at'] = stamp()
        if dst is not None:
            dst.close()
    return metadata


def _worker(source, destination, seconds, pages, pipe):
    try:
        outcome = dict(status='backed_up', **_backup_once(source, destination, seconds, pages))
    except BaseException as error:
        outcome = dict(status='failed', **_error(error))
    try:
        pipe.send(outcome)
    finally:
        pipe.close()


def _remove_partial(path):
    # Only diagnostic-owned files in an exclusively created run directory.
    for suffix in ('', '-wal', '-shm', '-journal'):
        candidate = Path(str(path)+suffix)
        if candidate.exists():
            candidate.unlink()


def _validate_destination(path, seconds=5):
    deadline = time.monotonic()+seconds
    with closing(sqlite3.connect(path, timeout=0.1)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        # This PRAGMA is ONLY for the diagnostic destination, never the source.
        if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
            raise SnapshotAbort('destination_journal_not_standalone')
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise SnapshotAbort('destination_integrity_failed')
    if any(Path(str(path)+s).exists() for s in ('-wal', '-shm', '-journal')):
        raise SnapshotAbort('destination_sidecars_present')
    return dict(quick_check='ok', size_bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def capture_snapshot(source, *, name, seconds=5.0, pages=64):
    """Explicit source; one bounded child; success only after local validation.

    No automatic operational source discovery or retry. OUTPUT_ROOT is fixed.
    The parent watchdog covers blocking open/backup calls as well as callbacks.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('invalid_name')
    if not math.isfinite(seconds) or not 0.05 <= seconds <= 30:
        raise ValueError('seconds_out_of_range')
    if type(pages) is not int or not 1 <= pages <= 256:
        raise ValueError('pages_out_of_range')
    source = Path(source).resolve(strict=True)
    if not source.is_file() or str(source).startswith(('\\\\', '//')):
        raise ValueError('existing_local_file_required')
    # Reject symlink/junction redirection of the output tree, before any writes.
    if OUTPUT_ROOT.resolve() != OUTPUT_ROOT.absolute():
        raise ValueError('redirected_output_root')
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    run = OUTPUT_ROOT/name
    run.mkdir()  # exclusive reservation, existing results never overwritten
    partial, final = run/'snapshot.incomplete.db', run/'snapshot.db'
    audit_path = run/'manifest.json'
    audit = dict(schema_version='online-snapshot-v1', status='pending', success=False,
                 source_path=str(source), snapshot_path=str(final), started_at=stamp(),
                 sqlite_version=sqlite3.sqlite_version, time_limit_seconds=seconds,
                 pages_per_step=pages, source_mode='ro', immutable=False,
                 source_sql_writes=False, source_files_copied=False,
                 source_locking_and_shm_possible=True, snapshot_time_kind='capture_interval',
                 snapshot_size_bytes=None, source_connection_released=False)

    def save():
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    save()  # Persist pending before any live connection; crash is not success.
    context = multiprocessing.get_context('spawn')
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(str(source), str(partial), seconds, pages, send))
    started = False
    try:
        started_clock = time.monotonic()
        process.start()
        started = True
        send.close()
        process.join(max(0, seconds-(time.monotonic()-started_clock)))
        if process.is_alive():
            raise SnapshotAbort('online_watchdog_timeout')
        if not receive.poll():
            raise SnapshotAbort('worker_exited_without_result')
        outcome = receive.recv()
        if outcome.pop('status') != 'backed_up':
            audit.update(outcome)
            raise SnapshotAbort(outcome['reason'])
        audit.update(outcome, source_connection_released=True)
        # The child has exited; it cannot retain any live source handles.
        audit.update(_validate_destination(partial))
        if final.exists():
            raise SnapshotAbort('destination_exists')
        partial.rename(final)
        audit.update(status='success', success=True, snapshot_size_bytes=audit.pop('size_bytes'))
    except BaseException as error:
        audit.update(_error(error), status='cancelled' if isinstance(error, KeyboardInterrupt) else 'failed')
        # Stop only this diagnostic child, NEVER an operational process.
        if started and process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
        if not started or not process.is_alive():
            audit['source_connection_released'] = True
            try:
                _remove_partial(partial)
            except OSError:
                audit['partial_cleanup_failed'] = True
        else:
            audit['reason'] = 'worker_termination_unconfirmed'
        # KeyboardInterrupt is a recorded non-success, not a published snapshot.
    finally:
        receive.close()
        send.close()
        if started and not process.is_alive():
            audit['worker_exitcode'] = process.exitcode
            process.close()
        audit['finished_at'] = stamp()
        save()
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--name', required=True)
    parser.add_argument('--seconds', type=float, default=5.0)
    parser.add_argument('--pages', type=int, default=64)
    args = parser.parse_args(argv)
    try:
        result = capture_snapshot(args.source, name=args.name, seconds=args.seconds, pages=args.pages)
    except (OSError, ValueError) as error:
        print(json.dumps(dict(status='rejected', success=False, **_error(error))))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['success'] else 130 if result['status'] == 'cancelled' else 2


if __name__ == '__main__':
    raise SystemExit(main())
