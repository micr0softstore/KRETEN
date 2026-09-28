"""Synthetic-only tests based on Firka's two school-notice model schemas."""
import unittest
from unittest.mock import patch
from jinja2 import Environment

from kreta_utils import KretaAPIError, KretaUtils
from view_models import plain_text


class SchoolNoticeAPITests(unittest.TestCase):
    def setUp(self):
        self.api = KretaUtils(klik_id='synthetic-school')
        self.network = patch('requests.sessions.Session.request',
                             side_effect=AssertionError('Live requests forbidden in synthetic tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_firka_info_board_fields_are_preserved(self):
        payload = [{'Uid': 'note-1', 'Cim': 'Synthetic class update', 'Datum': '2026-09-23T22:00:00Z',
                    'KeszitesDatuma': '2026-09-22T22:00:00Z', 'KeszitoTanarNeve': 'Synthetic teacher',
                    'Tartalom': 'Synthetic plain text', 'TartalomFormazott': '<b>Other HTML</b>',
                    'Tipus': {'Nev': 'synthetic_type', 'Leiras': 'Synthetic type label'}}]
        with patch.object(self.api, '_get', return_value=payload) as get:
            result = self.api.get_school_notices('notes', '2026-09-01')
        get.assert_called_once_with('notes', {'datumTol': '2026-09-01'})
        self.assertEqual(result, [{'id': 'note-1', 'title': 'Synthetic class update',
                                 'date': '2026-09-24 00:00', 'author': 'Synthetic teacher',
                                 'text': 'Synthetic plain text', 'source_label': 'Feljegyzések',
                                 'type_label': 'Synthetic type label'}])

    def test_firka_notice_board_fields_are_preserved(self):
        payload = [{'Uid': 'board-1', 'Cim': 'Synthetic school update',
                    'ErvenyessegKezdete': '2026-10-30T08:00:00Z', 'ErvenyessegVege': '2026-11-02T08:00:00Z',
                    'RogzitoNeve': 'Synthetic principal', 'TartalomText': 'Synthetic plain board text',
                    'Tartalom': '<b>Other HTML</b>'}]
        with patch.object(self.api, '_get', return_value=payload) as get:
            result = self.api.get_school_notices('board')
        get.assert_called_once_with('events', None)
        self.assertEqual(result, [{'id': 'board-1', 'title': 'Synthetic school update',
                                 'date': '2026-10-30 09:00', 'author': 'Synthetic principal',
                                 'text': 'Synthetic plain board text', 'source_label': 'Faliújság',
                                 'type_label': ''}])

    def test_source_and_date_validation_happens_before_transport(self):
        for source, start in [('invalid', None), ('notes', 'invalid')]:
            with self.subTest(source=source), patch.object(self.api, '_get') as get:
                with self.assertRaises(ValueError):
                    self.api.get_school_notices(source, start)
                get.assert_not_called()
        with self.assertRaises(ValueError):
            self.api.convert_school_notices([], 'invalid')

    def test_shape_errors_remain_visible_without_payload_text(self):
        for payload in ({'Message': 'synthetic-private-marker'}, [None], 'synthetic-private-marker'):
            with self.subTest(kind=type(payload).__name__):
                with self.assertRaises(KretaAPIError) as error:
                    self.api.convert_school_notices(payload, 'notes')
                self.assertEqual(error.exception.api_code, 'API_FORMAT')
                self.assertNotIn('synthetic-private-marker', str(error.exception))

    def test_empty_sources_are_empty_and_sparse_fields_have_fallbacks(self):
        self.assertEqual(self.api.convert_school_notices([], 'notes'), [])
        self.assertEqual(self.api.convert_school_notices([], 'board'), [])
        record = self.api.convert_school_notices([{'Uid': 'note-2', 'KeszitesDatuma': '2026-09-23T08:00:00Z',
                                                  'Tipus': {'Nev': 'Synthetic type'}}], 'notes')[0]
        self.assertEqual(record['title'], 'Nincs cím')
        self.assertEqual(record['date'], '2026-09-23 10:00')
        self.assertEqual(record['text'], '')
        self.assertEqual(record['type_label'], 'Synthetic type')

    def test_newest_first_order_uses_localized_dates(self):
        result = self.api.convert_school_notices([
            {'Uid': 'older', 'Datum': '2026-09-22T10:00:00Z'},
            {'Uid': 'unknown', 'Datum': 'invalid'},
            {'Uid': 'newer', 'Datum': '2026-09-23T10:00:00Z'},
        ], 'notes')
        self.assertEqual([item['id'] for item in result], ['newer', 'older', 'unknown'])

    def test_formatted_fallbacks_remain_inert_in_template_filter_contract(self):
        danger = '<p>Readable notice</p><img src=x onerror="syntheticAttack()"><script>syntheticAttack()</script>&lt;script&gt;'
        environment = Environment(autoescape=True)
        environment.filters['plain_text'] = plain_text
        template = environment.from_string('{{ notice.text|plain_text }}')
        for source, content_field in [('notes', 'TartalomFormazott'), ('board', 'Tartalom')]:
            with self.subTest(source=source):
                record = self.api.convert_school_notices([{'Uid': 'synthetic', content_field: danger}], source)[0]
                rendered = template.render(notice=record)
                self.assertIn('Readable notice', rendered)
                self.assertNotIn('<script', rendered)
                self.assertNotIn('<img', rendered)
                self.assertNotIn('onerror', rendered)

    def test_api_error_is_not_disguised_as_an_empty_feed(self):
        with patch.object(self.api, '_get', side_effect=KretaAPIError('Synthetic failure', api_code='API_HTTP', status=500)):
            with self.assertRaises(KretaAPIError) as error:
                self.api.get_school_notices()
        self.assertEqual(error.exception.http_status, 500)


if __name__ == '__main__':
    unittest.main()
