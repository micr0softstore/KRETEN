"""Loopback development server with a private, file-based stop command."""
from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time

from werkzeug.serving import make_server


@contextmanager
def _control(instance_path, port):
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    directory = Path(instance_path) / 'local-server'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    lock = directory / f'{port}.lock'
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'w') as handle:
        os.fchmod(handle.fileno(), 0o600)
        yield handle, directory / f'{port}.run', directory / f'{port}.stop'


def _acquire(handle):
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _write(path, text):
    # A reader must see either the complete old value or complete new value.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.control-')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read(path):
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ''


def _listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.3)
        result = probe.connect_ex(('127.0.0.1', port))
        if result == 0:
            return True
        if result == errno.ECONNREFUSED:
            return False
        raise RuntimeError(f'Could not verify port {port}; shutdown is not confirmed.')


def run_local(app, instance_path, port=5050):
    """Serve locally until interrupted or stopped by this instance's stop file."""
    with _control(instance_path, port) as (lock, running, stopping):
        if not _acquire(lock):
            raise RuntimeError(f'A managed KRETÉN server is already running on port {port}.')
        # Bind before creating a marker: an unrelated listener is never managed.
        server = make_server('127.0.0.1', port, app, threaded=True)
        server.timeout = 0.3
        run_id = secrets.token_hex(16)
        try:
            stopping.unlink(missing_ok=True)
            _write(running, run_id)
            print(f'KRETÉN: http://127.0.0.1:{port} — stop with PORT={port} python3 app.py --stop', flush=True)
            while _read(stopping) != run_id:
                server.handle_request()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            running.unlink(missing_ok=True)
            stopping.unlink(missing_ok=True)


def stop_local(instance_path, port=5050, timeout=8):
    """Stop a managed server, reporting success only after its port is closed."""
    with _control(instance_path, port) as (lock, running, stopping):
        if _acquire(lock):
            if _listening(port):
                raise RuntimeError(
                    f'Port {port} is in use by an unmanaged server; it was not stopped. '
                    'Stop that older process before starting this version.')
            return f'No KRETÉN server is listening on port {port}.'
        run_id = _read(running)
        if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise RuntimeError('The server is starting or its control file is unavailable; retry shortly.')
        _write(stopping, run_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _acquire(lock):
                if _listening(port):
                    raise RuntimeError(f'The managed server exited, but port {port} is still in use.')
                return f'KRETÉN stopped; port {port} is closed.'
            time.sleep(0.1)
        raise RuntimeError(f'KRETÉN did not stop within {timeout} seconds; shutdown is not confirmed.')
