"""Server-side, worker-safe authentication sessions.

The browser stores only an opaque random handle. OAuth credentials stay in this
private SQLite database; passwords are never persisted. Keep the instance folder
on a local disk shared by the application's workers, outside the static tree.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

_TOKEN_FIELDS = {'access_token', 'refresh_token', 'token_type', 'scope', 'expires_in', 'expires_at'}


def clean_tokens(token_data):
    if not isinstance(token_data, dict) or not token_data.get('access_token'):
        raise ValueError('Hiányzó hozzáférési token.')
    result = {key: value for key, value in token_data.items() if key in _TOKEN_FIELDS}
    try:
        lifetime = max(0, float(result.get('expires_in', 3600)))
        result['expires_at'] = float(result.get('expires_at', time.time() + lifetime))
    except (TypeError, ValueError):
        result['expires_at'] = time.time() + 3600
    return result


def load_or_create_secret(path):
    """Use one persistent signing key across restarts/workers, created atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # flock avoids readers observing a partially-written key during worker startup.
    with path.open('a+b') as key_file:
        os.chmod(path, 0o600)
        fcntl.flock(key_file, fcntl.LOCK_EX)
        key_file.seek(0)
        secret = key_file.read().decode('ascii').strip()
        if not secret:
            secret = secrets.token_hex(32)
            key_file.write(secret.encode('ascii'))
            key_file.flush()
            os.fsync(key_file.fileno())
        return secret


class SessionStore:
    def __init__(self, path, lifetime=12 * 60 * 60):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_dir = self.path.parent / 'session-locks'
        self.lock_dir.mkdir(exist_ok=True, mode=0o700)
        self.lifetime = lifetime
        # Create with restrictive permissions before SQLite opens the database.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self._connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('''CREATE TABLE IF NOT EXISTS auth_sessions (
                session_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                institute_code TEXT NOT NULL,
                token_data TEXT NOT NULL,
                expires_at REAL NOT NULL
            )''')
            connection.execute('DELETE FROM auth_sessions WHERE expires_at <= ?', (time.time(),))

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=25)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _key(sid):
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', sid):
            return None
        return hashlib.sha256(sid.encode('ascii')).hexdigest()

    def create(self, username, institute_code, token_data):
        token_data = clean_tokens(token_data)
        sid = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute('DELETE FROM auth_sessions WHERE expires_at <= ?', (time.time(),))
            connection.execute('INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?)', (
                self._key(sid), username, institute_code, json.dumps(token_data), time.time() + self.lifetime,
            ))
        return sid

    def get(self, sid):
        key = self._key(sid)
        if key is None:
            return None
        with self._connect() as connection:
            row = connection.execute('SELECT * FROM auth_sessions WHERE session_hash = ? AND expires_at > ?',
                                     (key, time.time())).fetchone()
        if row is None:
            return None
        return {'username': row['username'], 'institute_code': row['institute_code'],
                'token_data': json.loads(row['token_data']), 'expires_at': row['expires_at']}

    def delete(self, sid):
        key = self._key(sid)
        if key:
            with self._connect() as connection:
                connection.execute('DELETE FROM auth_sessions WHERE session_hash = ?', (key,))

    @contextmanager
    def _refresh_lock(self, key):
        # Keep lock files in place: unlinking could let concurrent workers lock
        # different inodes for the same session. Files contain no user data.
        lock_path = self.lock_dir / key
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'a') as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield

    def ensure_fresh(self, sid, refresher, force=False, stale_access_token=None):
        """Refresh one session atomically across workers without holding a DB lock.

        refresher(institute_code, token_data) must return new OAuth token data.
        stale_access_token prevents several simultaneous 401s rotating the same
        user's refresh token more than once. Revocation can never be undone here.
        """
        key = self._key(sid)
        if key is None:
            return None
        record = self.get(sid)
        if not record:
            return None
        if not force and record['token_data']['expires_at'] > time.time() + 60:
            return record
        with self._refresh_lock(key):
            record = self.get(sid)
            if not record:
                return None
            tokens = record['token_data']
            if stale_access_token and tokens['access_token'] != stale_access_token:
                return record
            if not force and tokens['expires_at'] > time.time() + 60:
                return record
            updated = clean_tokens(refresher(record['institute_code'], dict(tokens)))
            if not updated.get('refresh_token') and tokens.get('refresh_token'):
                updated['refresh_token'] = tokens['refresh_token']
            with self._connect() as connection:
                changed = connection.execute(
                    'UPDATE auth_sessions SET token_data = ? WHERE session_hash = ? AND expires_at > ?',
                    (json.dumps(updated), key, time.time()),
                ).rowcount
            if not changed:
                return None
            record['token_data'] = updated
            return record
