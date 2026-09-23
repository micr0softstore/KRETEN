from pathlib import Path
import errno
from unittest.mock import patch
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import local_server
from local_server import run_local, stop_local, _listening


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.instance = Path(self.temporary.name)
        self.process = None
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]

    def tearDown(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.communicate(timeout=5)
        self.temporary.cleanup()

    def start(self):
        script = ('from local_server import run_local; import sys; '
                  'app = lambda env, start: (start("200 OK", [("Content-Type", "text/plain")]), [b"ok"])[1]; '
                  'run_local(app, sys.argv[1], int(sys.argv[2]))')
        self.process = subprocess.Popen(
            [sys.executable, '-c', script, str(self.instance), str(self.port)],
            cwd=Path(local_server.__file__).parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail('Test server exited before startup.')
            if (self.instance / 'local-server' / f'{self.port}.run').exists() and _listening(self.port):
                return
            time.sleep(0.03)
        self.fail('Test server failed to start.')

    def test_stop_confirms_closed_port_and_process_exit(self):
        self.start()
        message = stop_local(self.instance, self.port)
        self.assertIn('is closed', message)
        self.assertFalse(_listening(self.port))
        self.assertEqual(self.process.wait(timeout=3), 0)
        self.assertFalse((self.instance / 'local-server' / f'{self.port}.run').exists())

    def test_unverifiable_probe_does_not_report_shutdown(self):
        with patch('local_server.socket.socket') as socket_type:
            socket_type.return_value.__enter__.return_value.connect_ex.return_value = errno.EACCES
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                stop_local(self.instance, self.port)

    def test_rejects_ephemeral_port(self):
        with self.assertRaises(ValueError):
            stop_local(self.instance, 0)

    def test_refuses_unmanaged_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.port))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, 'unmanaged'):
                stop_local(self.instance, self.port)
            self.assertTrue(_listening(self.port))

    def test_already_stopped_is_reported_truthfully(self):
        self.assertIn('No KRETÉN server', stop_local(self.instance, self.port))

    def test_lock_blocks_duplicate_start(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError, 'already running'):
            run_local(None, self.instance, self.port)
        self.assertTrue(_listening(self.port))
        stop_local(self.instance, self.port)

    def test_control_permissions_and_old_stop_marker(self):
        self.start()
        directory = self.instance / 'local-server'
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for suffix in ('lock', 'run'):
            self.assertEqual((directory / f'{self.port}.{suffix}').stat().st_mode & 0o777, 0o600)
        (directory / f'{self.port}.stop').write_text('0' * 32)
        time.sleep(0.5)
        self.assertTrue(_listening(self.port))
        stop_local(self.instance, self.port)


if __name__ == '__main__':
    unittest.main()
