"""Deterministic Windows startup sharing-lock/partial-write regression cases."""
import errno
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from browser_startup_helpers import wait_for_devtools_port


class Clock:
    def __init__(self):
        self.time = 0
        self.sleeps = []
    def __call__(self):
        return self.time
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.time += seconds


class BrowserStartupTests(unittest.TestCase):
    def setUp(self):
        self.process = Mock()
        self.process.poll.return_value = None
        self.clock = Clock()
        self.profile = Path('isolated-browser-profile')

    def wait(self, deadline=.2):
        return wait_for_devtools_port(self.profile,self.process,deadline,
                                      clock=self.clock,pause=self.clock.sleep)

    def test_windows_sharing_violation_then_readable_file(self):
        locked = PermissionError(errno.EACCES,'sharing violation')
        locked.winerror = 32
        with patch.object(Path,'read_text',side_effect=[locked,'9222\n/devtools/browser/test']) as read:
            self.assertEqual(self.wait(),'9222')
        self.assertEqual(read.call_count,2)
        self.assertEqual(read.call_args.kwargs,{'encoding':'utf-8'})
        self.assertEqual(self.clock.sleeps,[.05])

    def test_missing_empty_and_partial_port_are_not_treated_as_ready(self):
        with patch.object(Path,'read_text',side_effect=[FileNotFoundError(),'','92','9222\n/devtools/browser/test']) as read:
            self.assertEqual(self.wait(),'9222')
        self.assertEqual(read.call_count,4)
        self.assertEqual(len(self.clock.sleeps),3)

    def test_permanent_permission_failure_is_bounded_and_not_ignored(self):
        with patch.object(Path,'read_text',side_effect=PermissionError('access denied')) as read:
            with self.assertRaisesRegex(AssertionError,'startup deadline'):
                self.wait(.12)
        self.assertEqual(read.call_count,3)
        self.assertLessEqual(self.clock.time,.12)

    def test_invalid_ports_fail_instead_of_connecting_to_an_unvalidated_value(self):
        for value in ('0\n','65536\n','abc\n','12x\n','\n','999999\n'):
            self.clock=Clock()
            with self.subTest(value=value),patch.object(Path,'read_text',return_value=value):
                with self.assertRaisesRegex(AssertionError,'startup deadline'):
                    self.wait(.1)

    def test_exited_browser_fails_without_reading_or_waiting(self):
        self.process.poll.return_value=1
        with patch.object(Path,'read_text') as read:
            with self.assertRaisesRegex(AssertionError,'browser exited'):
                self.wait()
        read.assert_not_called()
        self.assertEqual(self.clock.sleeps,[])

    def test_unrelated_io_error_is_not_hidden_by_startup_retry(self):
        failure=OSError(errno.EIO,'disk failure')
        with patch.object(Path,'read_text',side_effect=failure):
            with self.assertRaises(OSError) as raised:
                self.wait()
        self.assertIs(raised.exception,failure)
        self.assertEqual(self.clock.sleeps,[])

    def test_crlf_port_file_is_supported(self):
        with patch.object(Path,'read_text',return_value='65535\r\n/devtools/browser/test'):
            self.assertEqual(self.wait(),'65535')
        self.assertEqual(self.clock.sleeps,[])


if __name__=='__main__': unittest.main()
