"""Bounded debugger-port discovery for an owned Chromium/Edge test process."""
from pathlib import Path
import re
import time


def wait_for_devtools_port(profile, process, deadline, *, clock=time.monotonic, pause=time.sleep):
    """Existence alone does not mean Chromium has closed its startup file.

    Windows can reject reads while the writer holds a sharing lock. Missing,
    temporarily locked, or incomplete files are retried only within the caller's
    startup deadline. Permanent failures still fail the browser test.
    """
    path = Path(profile)/'DevToolsActivePort'
    while clock() < deadline:
        if process.poll() is not None:
            raise AssertionError('Headless browser exited before its debugger port was readable')
        try:
            text = path.read_text(encoding='utf-8')
        except (FileNotFoundError, PermissionError):
            pass
        else:
            # A newline terminates the port; do not accept a partially written number.
            first, separator, _ = text.partition('\n')
            first = first.rstrip('\r')
            if separator and re.fullmatch(r'[0-9]{1,5}', first) and 1 <= int(first) <= 65535:
                return first
        pause(min(.05, max(0, deadline-clock())))
    raise AssertionError('Headless browser debugger port remained unreadable/incomplete until startup deadline')
