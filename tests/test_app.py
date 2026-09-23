"""Offline Flask integration tests, with isolated disposable server sessions."""
import atexit
from datetime import date
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_api_class, UNSAFE_HTML

# Set the instance path BEFORE importing the application. Importing these tests
# must not create/read production keys, sessions, or an old refresh-token file.
_IMPORT_INSTANCE = tempfile.TemporaryDirectory(prefix='kreten-test-import-')
atexit.register(_IMPORT_INSTANCE.cleanup)
with patch.dict(os.environ, {'KRETEN_INSTANCE_PATH': _IMPORT_INSTANCE.name,
                            'SECRET_KEY': 'synthetic-test-only-signing-key'}):
    app_module = importlib.import_module('app')
from session_store import SessionStore
from view_models import school_year

PAGES = ['/dashboard', '/orarend', '/jegyek', '/jegyek/statisztika', '/hazi-feladatok', '/hianyzasok',
         '/fogadoorak', '/lep', '/profil', '/uzenetek', '/uzenetek/elkuldott', '/uzenetek/torolt',
         '/uzenetek/reszletek/1', '/beallitasok']


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kreten-test-session-')
        self.store = SessionStore(Path(self.temp.name) / 'sessions.sqlite3')
        self.fake = make_api_class()
        self.config = patch.dict(app_module.app.config, {'TESTING': True, 'WTF_CSRF_ENABLED': True,
                                                       'SECRET_KEY': 'synthetic-test-only-signing-key',
                                                       'SESSION_COOKIE_SECURE': False})
        self.store_patch = patch.object(app_module, 'store', self.store)
        self.api_patch = patch.object(app_module, 'KretaUtils', self.fake)
        self.config.start()
        self.store_patch.start()
        self.api_patch.start()
        self.client = app_module.app.test_client()
        # Accidentally invoking a real transport makes any test fail immediately.
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Real network access forbidden in offline tests'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.api_patch.stop()
        self.store_patch.stop()
        self.config.stop()
        self.temp.cleanup()

    def token(self, client=None, path='/login'):
        page = (client or self.client).get(path)
        self.assertEqual(page.status_code, 200)
        element = BeautifulSoup(page.data, 'html.parser').select_one('meta[name="csrf-token"]')
        self.assertIsNotNone(element)
        return element['content']

    def login(self, client=None, code='school-a', username='synthetic-user'):
        client = client or self.client
        result = client.post('/login', data={'username': username, 'password': 'synthetic-password-marker',
                             'institute_code': code, 'csrf_token': self.token(client)})
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.location.endswith('/dashboard'))
        return result

    def authenticated_post(self, path, payload=None, client=None):
        client = client or self.client
        csrf = self.token(client, '/beallitasok')
        return client.post(path, json=payload or {}, headers={'X-CSRFToken': csrf})

    def test_every_major_page_renders_populated_data(self):
        self.login()
        for path in PAGES:
            with self.subTest(path=path):
                result = self.client.get(path)
                self.assertEqual(result.status_code, 200)
                self.assertIn('KRETÉN', result.get_data(as_text=True))
                self.assertIn('no-store', result.headers['Cache-Control'])
                self.assertEqual(result.headers['X-Frame-Options'], 'DENY')
        self.assertIn('Matematika', self.client.get('/jegyek').get_data(as_text=True))
        self.assertIn('Minta Diák', self.client.get('/dashboard').get_data(as_text=True))

    def test_read_only_bank_section_hides_write_controls(self):
        self.login()
        with patch.object(self.fake, 'get_student_data', return_value={'bank_read_only': True}):
            page = BeautifulSoup(self.client.get('/profil').data, 'html.parser')
        self.assertIsNone(page.select_one('form[action="/profil/bankszamla"]'))
        self.assertIsNone(page.select_one('[data-mutation-url="/profil/bankszamla"]'))
        self.assertIn('csak olvashatónak', page.get_text())
        self.assertIsNotNone(page.select_one('a[href="https://school-a.e-kreta.hu/"]'))

    def test_consultation_access_denied_is_not_an_empty_appointment_list(self):
        self.login()
        with patch.object(self.fake, 'get_consulting_hours', side_effect=app_module.KretaAPIError('synthetic-private-marker', api_code='API_HTTP', status=403)):
            body = self.client.get('/fogadoorak').get_data(as_text=True)
        self.assertIn('Ehhez a fiókhoz nem érhető el', body)
        self.assertNotIn('Jelenleg nincs elérhető fogadóóra.', body)
        self.assertNotIn('synthetic-private-marker', body)

    def test_school_notices_are_default_and_mailbox_remains_separate(self):
        self.login()
        page = BeautifulSoup(self.client.get('/uzenetek').data, 'html.parser')
        self.assertEqual(len(page.select('details.school-notice')), 2)
        self.assertIn('Minta feljegyzés', page.get_text())
        self.assertIn('Minta faliújság-hír', page.get_text())
        self.assertIn('teljes szövege', page.get_text())
        inbox = BeautifulSoup(self.client.get('/uzenetek/beerkezett').data, 'html.parser')
        self.assertIsNotNone(inbox.select_one('a.message-row'))
        self.assertNotIn('Minta feljegyzés', inbox.get_text())

    def test_every_major_page_has_a_working_empty_state(self):
        with patch.object(app_module, 'KretaUtils', make_api_class(empty=True)):
            self.login()
            for path in PAGES:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
        body = self.client.get('/beallitasok').get_data(as_text=True)
        self.assertIn('KRETÉN', body)

    def test_marks_and_statistics_regression_populated_and_empty(self):
        for empty in (False, True):
            with self.subTest(empty=empty), patch.object(app_module, 'KretaUtils', make_api_class(empty=empty)):
                client = app_module.app.test_client()
                self.login(client)
                for path in ('/jegyek', '/jegyek/statisztika'):
                    result = client.get(path)
                    self.assertEqual(result.status_code, 200)
                    self.assertNotIn("'max' is undefined", result.get_data(as_text=True))
                result = self.authenticated_post('/api/jegyek/statisztika', {'subject': 'all', 'period': 'all'}, client)
                self.assertEqual(result.status_code, 200)
                data = result.get_json()
                self.assertEqual(len(data['grade_distribution']['values']), 5)
                self.assertEqual(data['overall_stats']['count'], 0 if empty else 24)
                self.assertEqual(sum(data['grade_distribution']['values']), data['overall_stats']['count'])

    def test_external_html_cannot_create_scripts_event_handlers_or_javascript_links(self):
        with patch.object(app_module, 'KretaUtils', make_api_class(unsafe=True)):
            self.login()
            for path in ('/dashboard', '/hazi-feladatok', '/uzenetek', '/uzenetek/reszletek/1', '/lep'):
                with self.subTest(path=path):
                    result = self.client.get(path)
                    self.assertEqual(result.status_code, 200)
                    page = BeautifulSoup(result.data, 'html.parser')
                    self.assertIsNone(page.select_one('script#fixture-xss'))
                    self.assertIsNone(page.select_one('[onerror]'))
                    self.assertFalse(any(str(a.get('href', '')).lower().startswith('javascript:') for a in page.select('a[href]')))
                    self.assertIn('Biztonsági teszt', result.get_data(as_text=True))

    def test_grade_metadata_is_autoescaped(self):
        fake = make_api_class()
        original = fake.get_grades
        def unsafe_grades(client, start, end):
            data = original(client, start, end)
            data[0].update(subject=UNSAFE_HTML, topic=UNSAFE_HTML, teacher=UNSAFE_HTML)
            return data
        with patch.object(fake, 'get_grades', unsafe_grades), patch.object(app_module, 'KretaUtils', fake):
            self.login()
            for path in ('/jegyek', '/jegyek/statisztika'):
                page = BeautifulSoup(self.client.get(path).data, 'html.parser')
                self.assertIsNone(page.select_one('script#fixture-xss'))
                self.assertIsNone(page.select_one('[onerror]'))

    def test_csrf_rejects_login_and_mutations_without_token(self):
        denied = self.client.post('/login', data={'username': 'synthetic-user', 'password': 'synthetic-password', 'institute_code': 'school-a'})
        self.assertEqual(denied.status_code, 302)
        self.assertTrue(denied.location.endswith('/login'))
        self.assertFalse(any(call[0] == 'login' for call in self.fake.calls))
        self.login()
        for path in ('/api/refresh', '/profil/elerhetoseg', '/lep/engedelyezes/synthetic-event'):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={'isPermitted': True}).status_code, 400)
        self.client.post('/logout')
        self.assertEqual(self.client.get('/profil').status_code, 200)

    def test_two_clients_same_username_different_schools_and_independent_logout(self):
        second = app_module.app.test_client()
        self.login(code='school-a')
        self.login(second, code='school-b')
        first_body = self.client.get('/profil').get_data(as_text=True)
        second_body = second.get('/profil').get_data(as_text=True)
        self.assertIn('Minta Diák · school-a', first_body)
        self.assertNotIn('school-b', first_body)
        self.assertIn('Minta Diák · school-b', second_body)
        self.assertNotIn('school-a', second_body)
        with self.client.session_transaction() as session:
            first_sid = session['_user_id']
        with second.session_transaction() as session:
            second_sid = session['_user_id']
        self.assertNotEqual(first_sid, second_sid)
        csrf = self.token(path='/beallitasok')
        self.assertEqual(self.client.post('/logout', data={'csrf_token': csrf}).status_code, 302)
        self.assertIsNone(self.store.get(first_sid))
        self.assertIsNotNone(self.store.get(second_sid))
        self.assertEqual(self.client.get('/profil').status_code, 302)
        self.assertEqual(second.get('/profil').status_code, 200)

    def test_signed_browser_cookie_contains_no_credentials_or_identity(self):
        result = self.login()
        cookie = self.client.get_cookie(app_module.app.config['SESSION_COOKIE_NAME'])
        decoded = app_module.app.session_interface.get_signing_serializer(app_module.app).loads(cookie.value)
        serialized = json.dumps(decoded)
        for private in ('synthetic-user', 'synthetic-password', 'synthetic-access', 'synthetic-refresh', 'school-a', 'user_data'):
            self.assertNotIn(private, serialized)
        self.assertEqual(len(decoded['_user_id']), 43)
        self.assertIn('HttpOnly', result.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', result.headers['Set-Cookie'])

    def test_failed_login_never_echoes_upstream_body_or_credentials(self):
        with patch.object(app_module, 'KretaUtils', make_api_class(failures={'login'})):
            result = self.client.post('/login', data={'username': 'synthetic-user', 'password': 'synthetic-password-marker',
                'institute_code': 'school-a', 'csrf_token': self.token()}, follow_redirects=True)
        self.assertEqual(result.status_code, 200)
        body = result.get_data(as_text=True)
        self.assertIn('Nem sikerült bejelentkezni', body)
        self.assertNotIn('synthetic-private-exception-body', body)
        self.assertNotIn('synthetic-password-marker', body)
        self.assertNotIn('synthetic-user', body)

    def test_failed_dashboard_subsection_keeps_other_sections_usable(self):
        with patch.object(app_module, 'KretaUtils', make_api_class(failures={'get_grades'})):
            self.login()
            result = self.client.get('/dashboard')
        self.assertEqual(result.status_code, 200)
        body = result.get_data(as_text=True)
        self.assertIn('Minta Diák', body)
        self.assertIn('munkafüzet', body)
        self.assertIn('Hibakód: GRADES / CLIENT_ERROR', body)
        self.assertNotIn('synthetic-private-exception-body', body)

    def test_api_failure_does_not_echo_upstream_exception(self):
        with patch.object(app_module, 'KretaUtils', make_api_class(failures={'get_grades', 'update_contact_info'})):
            self.login()
            for path in ('/api/jegyek/statisztika', '/profil/elerhetoseg'):
                result = self.authenticated_post(path, {'subject': 'all', 'period': 'all'})
                self.assertEqual(result.status_code, 502)
                self.assertNotIn('synthetic-private-exception-body', result.get_data(as_text=True))

    def test_period_calculations_keep_the_same_school_year_across_new_year(self):
        cases = [(date(2026, 1, 15), '2025-09-01', '2026-01-31', '2026-02-01', '2026-08-31'),
                 (date(2026, 9, 23), '2026-09-01', '2027-01-31', '2027-02-01', '2027-08-31')]
        self.login()
        for current, start, first_end, second_start, end in cases:
            self.assertEqual(school_year('all', current), (start, end))
            self.assertEqual(school_year('first', current), (start, first_end))
            self.assertEqual(school_year('second', current), (second_start, end))
            with patch('view_models.today', return_value=current):
                for period, expected in [('all', (start, end)), ('first', (start, first_end)), ('second', (second_start, end))]:
                    self.fake.calls.clear()
                    result = self.authenticated_post('/api/jegyek/statisztika', {'period': period, 'subject': 'all'})
                    self.assertEqual(result.status_code, 200)
                    call = next(call for call in self.fake.calls if call[0] == 'get_grades')
                    self.assertEqual(call[2:], expected)

    def test_unavailable_reservation_does_not_claim_success(self):
        self.login()
        result = self.authenticated_post('/fogadoora/foglalas/synthetic-id')
        self.assertEqual(result.status_code, 501)
        self.assertNotIn('success', result.get_json())

    def test_refresh_never_returns_oauth_tokens_to_browser(self):
        self.login()
        result = self.authenticated_post('/api/refresh')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json(), {'success': True})
        self.assertNotIn('synthetic-access', result.get_data(as_text=True))

    def test_unauthenticated_pages_redirect_and_api_returns_401(self):
        for path in PAGES:
            self.assertEqual(self.client.get(path).status_code, 302)
        token = self.token()
        result = self.client.post('/api/refresh', json={}, headers={'X-CSRFToken': token})
        self.assertEqual(result.status_code, 401)

    def test_institution_route_returns_code_independently_from_identifier(self):
        result_data = {'institutions': [{'name': 'Synthetic School', 'code': 'bit-edu', 'id': '910018', 'city': '',
                                         'label': 'Synthetic School (bit-edu · 910018)'}], 'source': 'kreta-search', 'stale': False}
        with patch.object(app_module.directory, 'get', return_value=result_data) as directory:
            result = self.client.get('/api/institutions?q=Biatorb%C3%A1gy')
        directory.assert_called_once_with('Biatorbágy')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json(), result_data)


if __name__ == '__main__':
    unittest.main()
