"""Nonblocking local process lock, shared by ranking and quote entrypoints.

The OS releases the handle on process death. Durable SQLite request claims are
independent and are never cleared by this lock. Scope: resolved DB + environment.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import threading

_held = {}
_mutex = threading.RLock()


@contextmanager
def api_resource(path, environment):
    target = Path(str(Path(path).resolve()) + '.' + environment + '.api.lock')
    key = os.path.normcase(str(target))
    owner = threading.get_ident()
    with _mutex:
        if key in _held:
            if _held[key][0] != owner:
                raise ValueError('같은 DB/환경의 API 실행 자원이 사용 중입니다.')
            _held[key][1] += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = target.open('a+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    if target.stat().st_size == 0:
                        handle.write(b'0')
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise ValueError('같은 DB/환경의 API 실행 자원이 사용 중입니다.') from None
            _held[key] = [owner, 1, handle]
    try:
        yield
    finally:
        with _mutex:
            _held[key][1] -= 1
            if _held[key][1] == 0:
                handle = _held.pop(key)[2]
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                handle.close()


def exclusive_quotes(function):
    @wraps(function)
    def wrapped(path, client, *args, **kwargs):
        if not Path(path).is_file():
            return function(path, client, *args, **kwargs)
        with api_resource(path, kwargs.get('environment', 'mock')):
            return function(path, client, *args, **kwargs)
    return wrapped
