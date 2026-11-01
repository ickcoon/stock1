"""Opt-in native Windows TLS for independent weekly sessions; no global patch."""
import os
import ssl
from requests.adapters import HTTPAdapter


class WindowsTrustAdapter(HTTPAdapter):
    def __init__(self):
        if os.name != 'nt':
            raise ValueError('weekly_windows_trust_requires_windows')
        try:
            import truststore
        except ImportError:
            raise ValueError('weekly_windows_trust_dependency_required') from None
        self.context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.check_hostname = True
        super().__init__(max_retries=0)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        if verify is not True:
            raise ValueError('weekly_tls_verification_required')
        host, options = super().build_connection_pool_key_attributes(request, verify, cert)
        options['ssl_context'] = self.context
        options['cert_reqs'] = ssl.CERT_REQUIRED
        return host, options


def configure_windows_trust(session):
    """Call only on an isolated weekly/OAuth session before any requests."""
    adapter = WindowsTrustAdapter()
    session.trust_env = False
    session.verify = True
    session.mount('https://', adapter)
    return adapter
