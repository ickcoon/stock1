"""Opt-in user-scoped Windows DPAPI cache. No credentials or plaintext on disk."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from .api_resource import api_resource
from .errors import TokenManagementError


class WindowsDPAPI:
    def __init__(self):
        if os.name != 'nt':
            raise TokenManagementError('windows_protection_required')

    @staticmethod
    def _crypt(data, decrypt=False):
        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
        buffer = ctypes.create_string_buffer(data)
        incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        # UI_FORBIDDEN only: never LOCAL_MACHINE, never a prompt.
        if not fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise TokenManagementError('cache_protection_failed')
        try:
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            kernel.LocalFree(outgoing.pbData)

    def protect(self, value):
        return self._crypt(value)

    def unprotect(self, value):
        return self._crypt(value, decrypt=True)


class ProtectedTokenCache:
    def __init__(self, credentials, *, root=None, protector=None, lock_timeout=2.0):
        self.environment = credentials.environment
        self.binding = hashlib.sha256(json.dumps([credentials.environment, credentials.app_key,
            credentials.secret_key], separators=(',', ':')).encode()).hexdigest()
        # Lock identity ignores secret rotation so the same app/environment serializes.
        identity = hashlib.sha256((credentials.environment+'\0'+credentials.app_key).encode()).hexdigest()
        if root is None:
            if not os.environ.get('LOCALAPPDATA'):
                raise TokenManagementError('user_cache_directory_missing')
            root = Path(os.environ['LOCALAPPDATA'])/'Stock1'/'token-cache'
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise TokenManagementError('cache_redirected')
        self.protector = protector if protector is not None else WindowsDPAPI()
        self.path = self.root/(self.environment+'-'+identity+'.token-cache.bin')
        self.lock_path = self.root/identity
        if not 0 < lock_timeout <= 5:
            raise ValueError('invalid_cache_lock_timeout')
        self.lock_timeout = lock_timeout

    @contextmanager
    def locked(self):
        if self.root.resolve() != self.root:
            raise TokenManagementError('cache_redirected')
        deadline = time.monotonic()+self.lock_timeout
        resource = None
        while resource is None:
            try:
                attempt = api_resource(self.lock_path, self.environment)
                attempt.__enter__()
                resource = attempt
            except ValueError:
                if time.monotonic() >= deadline:
                    raise TokenManagementError('cache_busy') from None
                time.sleep(0.05)
            except OSError:
                raise TokenManagementError('cache_unavailable') from None
        try:
            yield
        finally:
            resource.__exit__(None, None, None)

    def load(self):
        try:
            if not self.path.exists():
                return None
            if self.path.is_symlink() or self.path.resolve() != self.path or self.path.stat().st_size > 65536:
                raise ValueError()
            data = json.loads(self.protector.unprotect(self.path.read_bytes()).decode('utf-8'))
            if data.get('version') != 1 or data.get('environment') != self.environment or data.get('binding') != self.binding:
                raise ValueError()
            if not isinstance(data.get('state'), dict):
                raise ValueError()
            return data['state']
        except Exception:
            raise TokenManagementError('cache_unreadable_or_scope_mismatch') from None

    def save(self, state):
        temporary = None
        try:
            if self.path.is_symlink() or self.root.resolve() != self.root:
                raise ValueError()
            data = json.dumps(dict(version=1, environment=self.environment, binding=self.binding,
                                   state=state), separators=(',', ':')).encode()
            encrypted = self.protector.protect(data)  # Encrypt before ANY disk write.
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.root, prefix='.encrypted-', delete=False) as target:
                temporary = Path(target.name)
                target.write(encrypted)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except Exception:
            raise TokenManagementError('cache_write_failed') from None
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass  # Any orphan contains ciphertext only.
