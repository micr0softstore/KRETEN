"""Entirely fictional API data for offline tests and an optional local preview.

Never enable this fixture in a production application. No real account data is
read, and every generated access/refresh token is deliberately synthetic.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

UNSAFE_HTML = '<script id="fixture-xss">window.fixtureXss=true</script><img src="x" onerror="window.fixtureXss=true"><a href="javascript:window.fixtureXss=true">Biztonsági teszt</a>'


def make_api_class(*, empty=False, failures=(), unsafe=False):
    failed = set(failures)
    calls = []

    class SyntheticKretaUtils:
        def __init__(self, user_name=None, password=None, klik_id=None):
            self.user_name = user_name
            self.klik_id = klik_id
            self.token_data = {}
            self.access_token = None
            self.refresh_handler = None

        @classmethod
        def from_tokens(cls, username, institute_code, token_data, refresh_handler=None):
            client = cls(username, None, institute_code)
            client.token_data = dict(token_data)
            client.access_token = token_data['access_token']
            client.refresh_handler = refresh_handler
            return client

        def login(self, username, password, institute_code):
            calls.append(('login', institute_code))
            if 'login' in failed:
                raise RuntimeError('synthetic-private-exception-body')
            self.token_data = {'access_token': f'synthetic-access-{institute_code}-{username}',
                               'refresh_token': f'synthetic-refresh-{institute_code}-{username}', 'expires_in': 3600}
            self.access_token = self.token_data['access_token']
            return dict(self.token_data)

        def refresh_tokens(self):
            self.token_data = {**self.token_data, 'access_token': self.access_token + '-rotated', 'expires_in': 3600}
            self.access_token = self.token_data['access_token']
            return dict(self.token_data)

        def _result(self, method, payload, blank=None, args=()):
            calls.append((method, self.klik_id, *args))
            if method in failed:
                raise RuntimeError('synthetic-private-exception-body')
            return deepcopy(blank if empty else payload)

        def get_student_data(self):
            return self._result('get_student_data', {
                'student_name': f'Minta Diák · {self.klik_id}', 'school_name': f'Minta Gimnázium · {self.klik_id}',
                'birth_date': '2008-01-01', 'email': 'synthetic@example.test', 'phone_number': '',
            }, {})

        def get_school_year_dates(self):
            return self._result('get_school_year_dates', [{'date': f'{_today().year + 1}-01-31', 'description': 'Első félév vége'}], [])

        def get_lessons(self, start_date, end_date):
            start = datetime.fromisoformat(start_date).date()
            end = datetime.fromisoformat(end_date).date()
            subjects = ['Matematika', 'Magyar irodalom', 'Angol nyelv', 'Történelem', 'Informatika']
            items = []
            for delta in range(min((end - start).days + 1, 14)):
                day = start + timedelta(days=delta)
                if day.weekday() >= 5:
                    continue
                for index, subject in enumerate(subjects, 1):
                    items.append({'id': f'synthetic-lesson-{day}-{index}', 'name': subject, 'subject': subject,
                        'start_time': f'{7+index:02}:00', 'end_time': f'{7+index:02}:45', 'date': day.isoformat(),
                        'index': index, 'classroom': f'{100 + index}', 'teacher': 'Minta Tanár',
                        'substitute_teacher': 'Helyettesítő Tanár' if index == 3 else '',
                        'topic': 'Ismétlés és gyakorlás', 'state': 'Elmaradt' if index == 4 else 'Megtartott',
                        'type': 'OrarendiOra', 'group': '10. A', 'yearly_ordinal': 5, 'description': ''})
            return self._result('get_lessons', items, [], args=(start_date, end_date))

        def get_grades(self, start_date, end_date):
            year = _today().year - (_today().month < 9)
            items = []
            for subject_index, subject in enumerate(['Matematika', 'Magyar irodalom', 'Angol nyelv', 'Történelem', 'Informatika', 'Testnevelés']):
                for i, number in enumerate([5, 4, 5, 3 + subject_index % 3]):
                    day = f'{year}-09-{1 + i * 4:02}'
                    if start_date <= day <= end_date:
                        items.append({'id': f'synthetic-grade-{subject_index}-{i}', 'date': day, 'topic': 'Gyakorló feladatok',
                            'type': 'EvkoziJegy', 'type_name': 'Évközi jegy', 'value': str(number), 'numeric_value': number,
                            'teacher': 'Minta Tanár', 'subject': subject, 'weight': 200 if i == 0 else 100})
            items.append({'id': 'synthetic-text-grade', 'date': f'{year}-09-15', 'topic': 'Közösségi munka',
                          'type': 'Szoveges', 'type_name': 'Szöveges értékelés', 'value': 'Dicséret', 'numeric_value': 0,
                          'teacher': 'Minta Tanár', 'subject': 'Magatartás', 'weight': 0})
            return self._result('get_grades', items, [], args=(start_date, end_date))

        def get_homework(self, start_date, end_date):
            return self._result('get_homework', [{'id': 'synthetic-homework', 'lesson_name': 'Matematika',
                'home_work_description': UNSAFE_HTML if unsafe else '<p>A munkafüzet 24. oldalának 1–3. feladata.</p>',
                'deadline': (_today() + timedelta(days=1)).isoformat(), 'date_added': _today().isoformat()}], [])

        def get_announced_tests(self, date=None):
            return self._result('get_announced_tests', [{'Tantargy': {'Nev': 'Angol nyelv'}, 'Temaja': 'Unit 2 – Revision',
                'Datum': (_today() + timedelta(days=2)).isoformat(), 'RogzitoTanarNeve': 'Minta Tanár',
                'Modja': {'Leiras': 'Írásbeli röpdolgozat'}}], [])

        def get_absences(self):
            return self._result('get_absences', [{'Tantargy': {'Nev': 'Történelem'}, 'Datum': _today().isoformat(),
                'Tipus': {'Leiras': 'Hiányzás'}, 'IgazolasAllapota': {'Leiras': 'Igazolt'}, 'KesesPercben': 0},
                {'Tantargy': {'Nev': 'Matematika'}, 'Datum': _today().isoformat(),
                'Tipus': {'Leiras': 'Késés'}, 'IgazolasAllapota': {'Leiras': 'Igazolatlan'}, 'KesesPercben': 8}], [])

        def get_consulting_hours(self):
            return self._result('get_consulting_hours', [{'tantargyNev': 'Matematika', 'tanarNev': 'Minta Tanár',
                'idopont': (_today() + timedelta(days=5)).isoformat() + 'T16:00', 'teremNev': '101', 'foglalas': None}], [])

        def get_lep_events(self):
            return self._result('get_lep_events', [{'eloadasId': 'synthetic-event', 'eloadasCim': 'Színházi előadás',
                'datum': (_today() + timedelta(days=8)).isoformat(), 'helyszin': 'Minta Színház', 'idotartam': 90,
                'leiras': UNSAFE_HTML if unsafe else 'Osztályprogram a színházban.', 'engedelyezheto': True}], [])

        def get_school_notices(self, source, start_date=None):
            return self._result('get_school_notices', [{'id': 'synthetic-' + source,
                'title': 'Minta feljegyzés' if source == 'notes' else 'Minta faliújság-hír',
                'author': 'Minta Tanár', 'date': _today().isoformat() + ' 10:30',
                'text': UNSAFE_HTML if unsafe else 'Minta iskolai értesítés teljes szövege.',
                'source_label': 'Feljegyzés' if source == 'notes' else 'Faliújság', 'type_label': ''}], [], args=(source, start_date))

        def get_messages(self, folder):
            return self._result('get_messages', [{'id': 1, 'message_id': 1, 'date': _today().isoformat() + ' 10:30',
                'sender_name': 'Minta Tanár', 'sender_title': 'Osztályfőnök', 'subject': 'Tájékoztató a következő hétről',
                'has_attachment': True, 'is_read': False}], [], args=(folder,))

        def get_message_details(self, message_id):
            return self._result('get_message_details', {'id': message_id, 'message': {
                'subject': 'Tájékoztató a következő hétről', 'sender_name': 'Minta Tanár',
                'date': _today().isoformat() + ' 10:30', 'recipients': [{'name': 'Minta Diák'}],
                'text': UNSAFE_HTML if unsafe else '<p>Kedves Diákok! A következő heti programot az órarendben találjátok.</p>',
                'attachments': [{'filename': 'synthetic-program.pdf'}],
            }}, None)

        def update_contact_info(self, **kwargs):
            return self._result('update_contact_info', True)

        def update_lep_event_permission(self, *args):
            return self._result('update_lep_event_permission', True)

        def update_bank_account(self, **kwargs):
            return self._result('update_bank_account', True)

        def delete_bank_account(self):
            return self._result('delete_bank_account', True)

    SyntheticKretaUtils.calls = calls
    return SyntheticKretaUtils


def _today():
    return datetime.now(ZoneInfo('Europe/Budapest')).date()


FakeKretaUtils = make_api_class()
