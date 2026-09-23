"""Offline login regressions using fictional credentials and upstream replies."""
import atexit
from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup
import requests

from kreta_utils import AuthenticationError, KretaUtils
from session_store import SessionStore


PRIVATE_MARKER = 'synthetic-private-upstream-marker'
FORM = ('<form><input name="__RequestVerificationToken" value="synthetic-csrf">'
        '<input name="ClientId" value="synthetic-client-field"></form>')
TOKENS = {'access_token': 'synthetic-access-token', 'refresh_token': 'synthetic-refresh-token',
          'expires_in': 3600}


def reply(status=200, *, text='', location=None, payload=None):
    """A real requests.Response gives HTTPError its actual failing response."""
    response = requests.Response()
    response.status_code = status
    response.url = 'https://idp.e-kreta.hu/synthetic-test'
    response.encoding = 'utf-8'
    response._content = (json.dumps(payload) if payload is not None else text).encode('utf-8')
    if location is not None:
        response.headers['Location'] = location
    return response


class LoginProtocolTests(unittest.TestCase):
    def setUp(self):
        self.api = KretaUtils('synthetic-user', None, 'school-a')
        self.http = MagicMock()
        self.context = MagicMock()
        self.context.__enter__.return_value = self.http
        self.session_patch = patch('requests.Session', return_value=self.context)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.network = patch('requests.sessions.Session.request',
                             side_effect=AssertionError('Real network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def login(self):
        return self.api.login('synthetic-user', 'synthetic-password', 'school-a')

    def assert_failure(self, code, status=None):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), self.assertRaises(AuthenticationError) as failure:
            self.login()
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(failure.exception.status, status)
        self.assertNotIn(PRIVATE_MARKER, failure.exception.public_message)
        self.assertNotIn(PRIVATE_MARKER, str(failure.exception))
        self.assertNotIn(PRIVATE_MARKER, output.getvalue())
        self.assertIsNone(self.api.access_token)
        return failure.exception

    def successful_flow(self, *, token_response=None):
        state = {}
        def get(url, **kwargs):
            if url.endswith('/Account/Login'):
                state['value'] = parse_qs(urlparse(kwargs['params']['ReturnUrl']).query)['state'][0]
                return reply(text=FORM)
            return reply(302, location=KretaUtils.REDIRECT_URI + '?' + urlencode({
                'state': state['value'], 'code': 'synthetic-authorization-code'}))
        self.http.get.side_effect = get
        self.http.post.side_effect = [reply(302, location='/connect/authorize/callback'),
                                      token_response if token_response is not None else reply(payload=TOKENS)]

    def test_network_tls_and_timeout_failures_are_distinct_and_private(self):
        for error, code in ((requests.ConnectionError(PRIVATE_MARKER), 'AUTH_NETWORK'),
                            (requests.exceptions.SSLError(PRIVATE_MARKER), 'AUTH_TLS'),
                            (requests.Timeout(PRIVATE_MARKER), 'AUTH_TIMEOUT')):
            with self.subTest(code=code):
                self.http.get.side_effect = error
                self.assert_failure(code)
        self.http.post.assert_not_called()

    def test_login_page_http_status_is_retained_without_body(self):
        self.http.get.return_value = reply(503, text=PRIVATE_MARKER)
        self.assert_failure('AUTH_PAGE', 503)
        self.http.post.assert_not_called()

    def test_missing_antiforgery_input_stops_before_password_submission(self):
        self.http.get.return_value = reply(text=PRIVATE_MARKER)
        self.assert_failure('AUTH_PAGE', 200)
        self.http.post.assert_not_called()

    def test_rejected_form_and_visible_challenge_are_distinguished(self):
        for body, code in ((PRIVATE_MARKER, 'AUTH_FORM'),
                           ('<div class="g-recaptcha"></div>' + PRIVATE_MARKER, 'AUTH_CHECK'),
                           ('<input autocomplete="one-time-code">' + PRIVATE_MARKER, 'AUTH_CHECK')):
            with self.subTest(code=code, body=body[:20]):
                self.http.reset_mock()
                self.http.get.return_value = reply(text=FORM)
                self.http.post.return_value = reply(text=body)
                self.assert_failure(code, 200)
                # A plain HTTP 200 can establish the IDP session. Try its OAuth
                # callback once before treating that response as rejection.
                self.assertEqual(self.http.get.call_count, 2 if code == 'AUTH_FORM' else 1)
                self.assertEqual(self.http.post.call_count, 1)

    def test_form_http_200_can_complete_cookie_based_authorization(self):
        state = {}
        self.http.cookies = requests.cookies.RequestsCookieJar()
        def get(url, **kwargs):
            if url.endswith('/Account/Login'):
                state['value'] = parse_qs(urlparse(kwargs['params']['ReturnUrl']).query)['state'][0]
                return reply(text=FORM)
            self.assertEqual(self.http.cookies.get('synthetic-idp-session'), 'synthetic-established-session')
            self.assertEqual(urlparse(url).path, '/connect/authorize/callback')
            self.assertNotIn('data', kwargs)
            return reply(302, location=KretaUtils.REDIRECT_URI + '?' + urlencode({
                'state': state['value'], 'code': 'synthetic-authorization-code'}))
        def post(url, **kwargs):
            if url.endswith('/account/login'):
                self.http.cookies.set('synthetic-idp-session', 'synthetic-established-session')
                return reply(200, text='<html>synthetic-login-completion</html>')
            self.assertTrue(url.endswith('/connect/token'))
            self.assertNotIn('Password', kwargs['data'])
            self.assertNotIn('UserName', kwargs['data'])
            return reply(payload=TOKENS)
        self.http.get.side_effect = get
        self.http.post.side_effect = post
        self.assertEqual(self.login()['access_token'], TOKENS['access_token'])
        self.assertEqual(self.http.get.call_count, 2)
        self.assertEqual(self.http.post.call_count, 2)
        self.assertEqual(sum(call.args[0].endswith('/account/login') for call in self.http.post.call_args_list), 1)

    def test_same_idp_intermediate_redirect_is_followed_without_auto_redirects(self):
        state = {}
        def get(url, **kwargs):
            if url.endswith('/Account/Login'):
                state['value'] = parse_qs(urlparse(kwargs['params']['ReturnUrl']).query)['state'][0]
                return reply(text=FORM)
            if url == 'https://idp.e-kreta.hu/account/continue':
                return reply(303, location='/connect/authorize/callback')
            if url == 'https://idp.e-kreta.hu/connect/authorize/callback':
                return reply(302, location=KretaUtils.REDIRECT_URI + '?' + urlencode({
                    'state': state['value'], 'code': 'synthetic-authorization-code'}))
            self.fail('Unexpected target')
        self.http.get.side_effect = get
        self.http.post.side_effect = [reply(302, location='/account/continue'), reply(payload=TOKENS)]
        self.assertEqual(self.login()['access_token'], TOKENS['access_token'])
        self.assertEqual(self.http.get.call_count, 3)
        self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in self.http.get.call_args_list))
        self.assertEqual(self.http.post.call_args_list[0].kwargs['headers']['User-Agent'], KretaUtils.LOGIN_USER_AGENT)
        submitted = self.http.post.call_args_list[0].kwargs['data']
        self.assertEqual(submitted['__RequestVerificationToken'], 'synthetic-csrf')
        self.assertEqual(submitted['InstituteCode'], 'school-a')

    def test_external_or_insecure_location_is_rejected_without_following(self):
        for target in ('https://untrusted.example/collect', '//untrusted.example/collect',
                       'http://idp.e-kreta.hu/account/continue',
                       'https://idp.e-kreta.hu@untrusted.example/collect'):
            with self.subTest(target=target):
                self.http.reset_mock()
                self.http.get.return_value = reply(text=FORM)
                self.http.post.return_value = reply(302, location=target)
                self.assert_failure('AUTH_REDIRECT')
                self.assertEqual(self.http.get.call_count, 1)
                self.assertEqual(self.http.post.call_count, 1)

    def test_token_http_failure_reports_its_own_status_without_upstream_details(self):
        self.successful_flow(token_response=reply(401, text=PRIVATE_MARKER))
        self.assert_failure('AUTH_TOKEN', 401)
        self.assertEqual(self.http.post.call_count, 2)

    def test_intermediate_http_failure_uses_failed_response_status(self):
        self.http.get.side_effect = [reply(text=FORM), reply(502, text=PRIVATE_MARKER)]
        self.http.post.return_value = reply(302, location='/account/continue')
        self.assert_failure('AUTH_REDIRECT', 502)
        self.assertEqual(self.http.post.call_count, 1)

    def test_invalid_token_json_has_safe_token_error(self):
        self.successful_flow(token_response=reply(text=PRIVATE_MARKER))
        self.assert_failure('AUTH_TOKEN')

    def test_token_exchange_must_not_follow_redirects(self):
        self.successful_flow(token_response=reply(307, location='https://untrusted.example/collect', payload=TOKENS))
        self.assert_failure('AUTH_TOKEN', 307)
        self.assertIs(self.http.post.call_args_list[-1].kwargs.get('allow_redirects'), False)

    def test_duplicate_oauth_state_is_rejected_before_token_exchange(self):
        state = {}
        def get(url, **kwargs):
            if url.endswith('/Account/Login'):
                state['value'] = parse_qs(urlparse(kwargs['params']['ReturnUrl']).query)['state'][0]
                return reply(text=FORM)
            return reply(302, location=KretaUtils.REDIRECT_URI + '?' + urlencode([
                ('state', state['value']), ('state', 'synthetic-unexpected-state'),
                ('code', 'synthetic-authorization-code')]))
        self.http.get.side_effect = get
        self.http.post.return_value = reply(302, location='/connect/authorize/callback')
        self.assert_failure('AUTH_STATE')
        self.assertEqual(self.http.post.call_count, 1)

    def test_refresh_token_must_not_follow_redirects(self):
        with patch('requests.post', return_value=reply(
                307, location='https://untrusted.example/collect', payload=TOKENS)) as post:
            with self.assertRaises(AuthenticationError) as failure:
                KretaUtils.refresh_token_data('school-a', TOKENS)
        self.assertEqual(failure.exception.code, 'AUTH_TOKEN')
        self.assertEqual(failure.exception.status, 307)
        self.assertIs(post.call_args.kwargs.get('allow_redirects'), False)


# Keep imports from ever touching the real application's session database.
_IMPORT_INSTANCE = tempfile.TemporaryDirectory(prefix='kreten-diagnostics-import-')
atexit.register(_IMPORT_INSTANCE.cleanup)
with patch.dict(os.environ, {'KRETEN_INSTANCE_PATH': _IMPORT_INSTANCE.name,
                            'SECRET_KEY': 'synthetic-test-only-signing-key'}):
    app_module = importlib.import_module('app')


class LoginDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kreten-diagnostics-session-')
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(Path(self.temp.name) / 'sessions.sqlite3')
        self.api = MagicMock()
        self.api.login.return_value = TOKENS.copy()
        patches = [patch.object(app_module, 'store', self.store),
                   patch.object(app_module, 'KretaUtils', return_value=self.api),
                   patch.dict(app_module.app.config, {'TESTING': True, 'WTF_CSRF_ENABLED': True,
                              'SECRET_KEY': 'synthetic-test-only-signing-key', 'SESSION_COOKIE_SECURE': False}),
                   patch('requests.sessions.Session.request', side_effect=AssertionError('Real network forbidden'))]
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)
        self.client = app_module.app.test_client()

    def submit(self):
        page = self.client.get('/login')
        self.assertEqual(page.status_code, 200)
        token = BeautifulSoup(page.data, 'html.parser').select_one('meta[name="csrf-token"]')['content']
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = self.client.post('/login', data={'username': 'synthetic-user',
                'password': 'synthetic-password', 'institute_code': 'school-a', 'csrf_token': token},
                follow_redirects=True)
        self.assertEqual(result.status_code, 200)
        text = result.get_data(as_text=True)
        self.assertNotIn(PRIVATE_MARKER, text)
        self.assertNotIn('synthetic-password', text)
        self.assertNotIn(PRIVATE_MARKER, output.getvalue())
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)
        return text

    def test_safe_auth_code_is_shown_instead_of_custom_exception_message(self):
        self.api.login.side_effect = AuthenticationError(PRIVATE_MARKER, code='AUTH_NETWORK')
        self.assertIn('AUTH_NETWORK', self.submit())

    def test_unexpected_client_exception_is_not_misreported_as_bad_credentials(self):
        self.api.login.side_effect = RuntimeError(PRIVATE_MARKER)
        self.assertIn('LOGIN_CLIENT', self.submit())

    def test_storage_failure_after_upstream_login_is_distinguished(self):
        with patch.object(self.store, 'create', side_effect=OSError(PRIVATE_MARKER)):
            text = self.submit()
        self.assertIn('LOGIN_STORAGE', text)
        self.assertNotIn('AUTH_FORM', text)
        self.api.login.assert_called_once()

    def test_partially_created_session_is_revoked_when_session_load_fails(self):
        created = []
        original_create = self.store.create
        def create(*args):
            sid = original_create(*args)
            created.append(sid)
            return sid
        with patch.object(self.store, 'create', side_effect=create), \
                patch.object(self.store, 'get', side_effect=OSError(PRIVATE_MARKER)):
            self.assertIn('LOGIN_STORAGE', self.submit())
        self.assertEqual(len(created), 1)
        self.assertIsNone(self.store.get(created[0]))


if __name__ == '__main__':
    unittest.main()
