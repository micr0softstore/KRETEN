"""Synthetic fixtures reflecting the published grouped consultation response."""
import unittest
from unittest.mock import patch

from kreta_utils import KretaAPIError, KretaUtils


def meeting(uid, start='2026-09-30T14:00:00Z', **changes):
    return {'Uid': uid, 'KezdoIdopont': start, 'VegIdopont': '2026-09-30T15:00:00Z',
            'Terem': {'Nev': 'Synthetic room'}, 'Idopontok': [], **changes}


class ConsultationAPITests(unittest.TestCase):
    def setUp(self):
        self.api = KretaUtils(klik_id='synthetic-school')
        self.network = patch('requests.sessions.Session.request',
                             side_effect=AssertionError('Live requests forbidden in synthetic tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_teacher_groups_are_flattened_and_localized_for_the_cards(self):
        payload = [
            {'Tanar': {'Nev': 'Synthetic teacher A'}, 'Fogadoorak': [meeting('one'), meeting('two')]},
            {'Tanar': {'Nev': 'Synthetic teacher B'}, 'Fogadoorak': [meeting('three', '2026-09-29T14:00:00Z')]},
        ]
        with patch.object(self.api, '_get', return_value=payload) as get:
            result = self.api.get_consulting_hours()
        get.assert_called_once_with('consulting_hours', {})
        self.assertEqual([row['uid'] for row in result], ['three', 'one', 'two'])
        self.assertEqual(result[0]['tanarNev'], 'Synthetic teacher B')
        self.assertEqual(result[1]['idopont'], '2026-09-30 16:00')
        self.assertEqual(result[1]['vegIdopont'], '2026-09-30 17:00')
        self.assertEqual(result[1]['teremNev'], 'Synthetic room')
        self.assertIsNone(result[1]['foglalas'])

    def test_only_user_reservations_are_displayed(self):
        slots = [{'KezdoIdopont': '2026-09-30T14:10:00Z', 'IsJelentkeztem': True},
                 {'KezdoIdopont': '2026-09-30T14:20:00Z', 'IsJelentkeztem': False}]
        payload = [{'Tanar': {'Nev': 'Synthetic teacher'}, 'Fogadoorak': [meeting('one', Idopontok=slots)]}]
        self.assertEqual(self.api.convert_consulting_hours(payload)[0]['foglalas'],
                         {'idopont': '2026-09-30 16:10'})

    def test_empty_teacher_groups_remain_empty(self):
        self.assertEqual(self.api.convert_consulting_hours([]), [])
        self.assertEqual(self.api.convert_consulting_hours([{'Tanar': {}, 'Fogadoorak': []}]), [])

    def test_bad_response_shape_is_not_shown_as_no_appointments(self):
        for payload in ({'Message': 'synthetic-private-marker'}, [None], [{'Fogadoorak': None}]):
            with self.subTest(kind=type(payload).__name__):
                with self.assertRaises(KretaAPIError) as error:
                    self.api.convert_consulting_hours(payload)
                self.assertEqual(error.exception.api_code, 'API_FORMAT')
                self.assertNotIn('synthetic-private-marker', str(error.exception))

    def test_permission_failure_is_preserved_for_page_diagnostics(self):
        with patch.object(self.api, '_get', side_effect=KretaAPIError('Synthetic refusal', api_code='API_HTTP', status=403)):
            with self.assertRaises(KretaAPIError) as error:
                self.api.get_consulting_hours()
        self.assertEqual(error.exception.http_status, 403)

    def test_explicit_date_filters_are_kept(self):
        with patch.object(self.api, '_get', return_value=[]) as get:
            self.api.get_consulting_hours('2026-09-23', '2026-10-07')
        get.assert_called_once_with('consulting_hours', {'datumTol': '2026-09-23', 'datumIg': '2026-10-07'})


if __name__ == '__main__':
    unittest.main()
