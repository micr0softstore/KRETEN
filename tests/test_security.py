"""Synthetic-only regressions; never contact a real account."""
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
import requests
from institutions import InstitutionDirectory, normalize_institutions, normalize_search_results, validate_institution_code
from kreta_utils import KretaUtils, KretaAPIError, AuthenticationError
from session_store import SessionStore, load_or_create_secret


def tokens(label, expires_in=3600):
    return {'access_token': 'synthetic-access-' + label, 'refresh_token': 'synthetic-refresh-' + label, 'expires_in': expires_in}


def response(status=200, payload=None):
    result = MagicMock()
    result.status_code = status
    result.json.return_value = payload
    result.text = 'synthetic-private-response-marker'
    result.headers = {}
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError('synthetic-private-error-marker')
    return result


def process_refresh(arguments):
    path, sid = arguments
    store = SessionStore(path)
    def rotate(code, data):
        with open(str(path) + '.refresh-count', 'a') as counter:
            counter.write('refresh\n')
        time.sleep(0.03)
        return tokens('process-new')
    return store.ensure_fresh(sid, rotate)['token_data']['access_token']


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'instance' / 'sessions.sqlite3'
        self.store = SessionStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_two_people_same_username_different_schools_are_isolated(self):
        first = self.store.create('synthetic-user', 'school-a', tokens('one'))
        second = self.store.create('synthetic-user', 'school-b', tokens('two'))
        self.assertNotEqual(first, second)
        other_worker = SessionStore(self.path)
        self.assertEqual(other_worker.get(first)['token_data']['access_token'], 'synthetic-access-one')
        self.assertEqual(other_worker.get(second)['institute_code'], 'school-b')
        self.store.delete(first)
        self.assertIsNone(other_worker.get(first))
        self.assertIsNotNone(other_worker.get(second))

    def test_opaque_handle_is_not_stored_in_database(self):
        sid = self.store.create('synthetic-user', 'school-a', tokens('one'))
        with self.store._connect() as connection:
            key = connection.execute('SELECT session_hash FROM auth_sessions').fetchone()[0]
        self.assertEqual(key, hashlib.sha256(sid.encode()).hexdigest())
        self.assertNotEqual(key, sid)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_password_and_arbitrary_fields_are_never_persisted(self):
        sid = self.store.create('synthetic-user', 'school-a', tokens('one') | {'password': 'synthetic-password', 'UserName': 'synthetic-marker'})
        self.assertNotIn('password', self.store.get(sid)['token_data'])
        self.assertNotIn('UserName', self.store.get(sid)['token_data'])

    def test_expired_or_malformed_session_is_rejected(self):
        expired = SessionStore(self.path, lifetime=-1).create('synthetic', 'school-a', tokens('one'))
        self.assertIsNone(self.store.get(expired))
        for invalid in (None, '', '../outside', 'x' * 43, {'bad': True}):
            self.assertIsNone(self.store.get(invalid))

    def test_refresh_is_serialized_across_independent_stores(self):
        sid = self.store.create('synthetic', 'school-a', tokens('old', -100))
        workers = [SessionStore(self.path) for _ in range(6)]
        count = []
        def refresh(code, data):
            count.append(code)
            self.assertEqual(data['refresh_token'], 'synthetic-refresh-old')
            time.sleep(0.03)
            return tokens('new')
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda worker: worker.ensure_fresh(sid, refresh), workers))
        self.assertEqual(count, ['school-a'])
        self.assertTrue(all(result['token_data']['access_token'] == 'synthetic-access-new' for result in results))

    def test_refresh_is_serialized_across_worker_processes(self):
        sid = self.store.create('synthetic', 'school-a', tokens('old', -100))
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('fork')) as executor:
            results = list(executor.map(process_refresh, [(self.path, sid)] * 4))
        self.assertEqual(results, ['synthetic-access-process-new'] * 4)
        self.assertEqual(Path(str(self.path) + '.refresh-count').read_text(), 'refresh\n')

    def test_simultaneous_unauthorized_responses_do_not_rotate_twice(self):
        sid = self.store.create('synthetic', 'school-a', tokens('old'))
        self.store.ensure_fresh(sid, lambda *_: tokens('new'), force=True, stale_access_token='synthetic-access-old')
        callback = MagicMock(side_effect=AssertionError('Must not rotate already-refreshed token'))
        self.store.ensure_fresh(sid, callback, force=True, stale_access_token='synthetic-access-old')
        callback.assert_not_called()

    def test_refresh_cannot_resurrect_a_logged_out_session(self):
        sid = self.store.create('synthetic', 'school-a', tokens('old', -100))
        def refresh(*_):
            self.store.delete(sid)
            return tokens('new')
        self.assertIsNone(self.store.ensure_fresh(sid, refresh))
        self.assertIsNone(self.store.get(sid))

    def test_failed_refresh_leaves_other_sessions_intact(self):
        first = self.store.create('synthetic-one', 'school-a', tokens('one', -100))
        second = self.store.create('synthetic-two', 'school-b', tokens('two'))
        with self.assertRaises(AuthenticationError):
            self.store.ensure_fresh(first, MagicMock(side_effect=AuthenticationError('expired')))
        self.assertEqual(self.store.get(second)['token_data']['access_token'], 'synthetic-access-two')

    def test_persistent_secret_is_shared_across_restarts(self):
        path = self.path.parent / 'secret-key'
        with ThreadPoolExecutor(max_workers=6) as executor:
            values = list(executor.map(lambda _: load_or_create_secret(path), range(6)))
        self.assertEqual(len(set(values)), 1)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = KretaUtils.from_tokens('synthetic-user', 'school-a', tokens('one'))

    def test_constructor_and_restore_never_log_in_or_read_a_global_file(self):
        with patch('requests.Session') as http, patch('builtins.open', side_effect=AssertionError('Unexpected file read')):
            new = KretaUtils('synthetic-user', 'synthetic-password', 'school-a')
            restored = KretaUtils.from_tokens('synthetic-user', 'school-a', tokens('one'))
        http.assert_not_called()
        self.assertFalse(hasattr(new, 'password'))
        self.assertEqual(restored.request_headers['Authorization'], 'Bearer synthetic-access-one')

    def test_request_does_not_log_sensitive_data_and_uses_timeout(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), patch('requests.request', return_value=response(payload=[])) as request:
            self.client.get_absences()
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(request.call_args.kwargs['timeout'], KretaUtils.TIMEOUT)
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_api_error_never_includes_response_body_or_raw_exception(self):
        for behavior in ({'return_value': response(500)}, {'side_effect': requests.ConnectionError('synthetic-private-marker')}):
            with patch('requests.request', **behavior), self.assertRaises(KretaAPIError) as failure:
                self.client.get_absences()
            self.assertNotIn('synthetic-private', str(failure.exception))

    def test_unauthorized_refresh_updates_only_current_instance(self):
        other = KretaUtils.from_tokens('synthetic-other', 'school-b', tokens('two'))
        self.client.refresh_handler = MagicMock(return_value=tokens('new'))
        with patch('requests.request', side_effect=[response(401), response(payload=[])]) as http:
            self.assertEqual(self.client.get_absences(), [])
        self.client.refresh_handler.assert_called_once_with('synthetic-access-one')
        self.assertEqual(http.call_args_list[1].kwargs['headers']['Authorization'], 'Bearer synthetic-access-new')
        self.assertEqual(other.access_token, 'synthetic-access-two')

    def test_login_preserves_code_exchange_and_uses_fresh_pkce(self):
        http = MagicMock()
        initial = response()
        initial.text = '<input name="__RequestVerificationToken" value="synthetic-csrf">'
        verifiers = []
        def get(url, **kwargs):
            if '/Account/Login' in url:
                return initial
            state = parse_qs(urlparse(url).query)['state'][0]
            callback = response(302)
            callback.headers = {'Location': KretaUtils.REDIRECT_URI + '?code=synthetic-code&state=' + state}
            return callback
        def post(url, **kwargs):
            if '/account/login' in url:
                return response(302)
            verifiers.append(kwargs['data']['code_verifier'])
            return response(payload=tokens('new'))
        http.get.side_effect = get
        http.post.side_effect = post
        context = MagicMock()
        context.__enter__.return_value = http
        with patch('requests.Session', return_value=context), patch('builtins.open', side_effect=AssertionError('Unexpected file access')):
            self.client.login('synthetic-user', 'synthetic-password', 'school-a')
            self.client.login('synthetic-user', 'synthetic-password', 'school-a')
        self.assertNotEqual(verifiers[0], verifiers[1])
        self.assertEqual(self.client.access_token, 'synthetic-access-new')

    def test_invalid_oauth_state_is_rejected(self):
        http = MagicMock()
        initial = response()
        initial.text = '<input name="__RequestVerificationToken" value="synthetic-csrf">'
        callback = response(302)
        callback.headers = {'Location': KretaUtils.REDIRECT_URI + '?code=synthetic&state=wrong'}
        http.get.side_effect = [initial, callback]
        http.post.return_value = response(302)
        context = MagicMock()
        context.__enter__.return_value = http
        with patch('requests.Session', return_value=context), self.assertRaises(AuthenticationError):
            self.client.login('synthetic-user', 'synthetic-password', 'school-a')
        self.assertEqual(http.post.call_count, 1)

    def test_missing_new_refresh_token_preserves_current_users_refresh(self):
        with patch('requests.post', return_value=response(payload={'access_token': 'synthetic-next', 'expires_in': 500})) as post:
            result = KretaUtils.refresh_token_data('school-a', tokens('one'))
        self.assertEqual(result['refresh_token'], 'synthetic-refresh-one')
        self.assertEqual(post.call_args.kwargs['data']['institute_code'], 'school-a')

    def test_numeric_textual_and_invalid_grades_are_safe(self):
        grades = self.client.convert_grades([
            {'SzamErtek': '5', 'SulySzazalekErteke': '200', 'RogzitesDatuma': '2026-09-01T12:00:00Z', 'Tantargy': {'Nev': 'Synthetic'}},
            {'SzovegesErtek': 'Dicséret', 'SulySzazalekErteke': None, 'RogzitesDatuma': None},
            {'SzovegesErtek': 'invalid', 'SzamErtek': 6, 'SulySzazalekErteke': 'bad', 'Tipus': None}, {'SzamErtek': None},
        ])
        self.assertEqual(len(grades), 3)
        self.assertEqual(grades[0]['numeric_value'], 5)
        self.assertEqual(grades[0]['weight'], 200)
        self.assertEqual(grades[1]['numeric_value'], 0)
        self.assertEqual(grades[2]['numeric_value'], 0)
        self.assertEqual(grades[1]['weight'], 100)

    def test_timetable_budapest_timezone_keeps_cancelled_lessons(self):
        lessons = self.client.convert_lessons([{'Tipus': {'Nev': 'ElmaradtOra'}, 'Allapot': {'Leiras': 'Elmaradt'},
            'KezdetIdopont': '2026-09-22T23:00:00Z', 'VegIdopont': '2026-09-22T23:45:00Z', 'Oraszam': '0', 'Tantargy': None}])
        self.assertEqual(lessons[0]['date'], '2026-09-23')
        self.assertEqual(lessons[0]['start_time'], '01:00')
        self.assertEqual(lessons[0]['state'], 'Elmaradt')


class InstitutionTests(unittest.TestCase):
    def test_public_directory_mapping_keeps_code_and_identifier_separate(self):
        rows = normalize_institutions([{'InstituteCode': 'BIT-EDU', 'Name': 'Synthetic School', 'InstituteId': 910018}])
        self.assertEqual(rows[0]['code'], 'bit-edu')
        self.assertEqual(rows[0]['id'], '910018')

    def test_institution_code_cannot_inject_hosts_or_paths(self):
        for code in ('example.org', '//localhost', 'bit-edu/path', 'user@host', '-invalid', 'invalid-', 'a' * 64, ''):
            with self.assertRaises(ValueError):
                validate_institution_code(code)
        self.assertEqual(validate_institution_code(' BIT-EDU '), 'bit-edu')

    def test_directory_caches_and_search_ignores_accents(self):
        directory = InstitutionDirectory()
        upstream = [{'instituteCode': 'school-a', 'name': 'Árvíztűrő Iskola', 'instituteId': 42, 'city': 'Budapest'}]
        with patch('requests.get', return_value=response(payload=upstream)) as http:
            result = directory.get('arvizturo')
            directory.get()
        self.assertEqual(result['institutions'][0]['code'], 'school-a')
        self.assertEqual(result['source'], 'kreta')
        http.assert_called_once()

    def test_official_search_html_keeps_verified_code_and_identifier(self):
        html = '<a href="#" class="dropdown-item" data-val="bit-edu">Biatorbágyi Innovatív Technikum és Gimnázium (bit-edu - 910018)</a>'
        rows = normalize_search_results(html + '<a data-val="//invalid">Invalid</a>')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['code'], 'bit-edu')
        self.assertEqual(rows[0]['id'], '910018')
        self.assertEqual(rows[0]['name'], 'Biatorbágyi Innovatív Technikum és Gimnázium')

    def test_live_search_fallback_is_cached_and_url_encoded(self):
        directory = InstitutionDirectory()
        upstream = response()
        upstream.text = '<a data-val="bit-edu">Synthetic (bit-edu - 910018)</a>'
        with patch('requests.get', side_effect=[requests.ConnectionError(), upstream]) as http:
            result = directory.get('Biatorbágy')
            cached = directory.get('Biatorbágy')
            blank = directory.get()
        self.assertEqual(result['source'], 'kreta-search')
        self.assertFalse(result['stale'])
        self.assertEqual(result['institutions'][0]['code'], 'bit-edu')
        self.assertEqual(result['institutions'][0]['id'], '910018')
        self.assertEqual(cached, result)
        self.assertEqual(blank['institutions'], [])
        self.assertEqual(http.call_count, 2)
        self.assertIn('Biatorb%C3%A1gy', http.call_args.args[0])
        self.assertEqual(http.call_args.kwargs['params'], {'showOnlyLive': 'true'})

    def test_unavailable_directory_has_no_automatic_school_and_accepts_manual_codes(self):
        directory = InstitutionDirectory()
        with patch('requests.get', side_effect=requests.ConnectionError()):
            result = directory.get()
        self.assertTrue(result['stale'])
        self.assertEqual(result['source'], 'fallback')
        self.assertEqual(result['institutions'], [])
        self.assertEqual(validate_institution_code('another-school'), 'another-school')

    def test_failed_search_does_not_invent_a_matching_school(self):
        directory = InstitutionDirectory()
        with patch('requests.get', side_effect=requests.ConnectionError()) as http:
            result = directory.get('bit-edu')
        self.assertEqual(http.call_count, 2)
        self.assertEqual(result, {'institutions': [], 'source': 'fallback', 'stale': True})


if __name__ == '__main__':
    unittest.main()
