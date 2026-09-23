"""Synthetic homework API regressions; never contact a live account."""
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from kreta_utils import KretaAPIError, KretaUtils


def homework(uid='synthetic-homework', **fields):
    return {'Uid': uid, 'TantargyNeve': 'Synthetic subject', 'Szoveg': 'Synthetic homework',
            'HataridoDatuma': '2026-09-29T22:00:00Z',
            'RogzitesIdopontja': '2026-09-22T22:00:00Z', **fields}


class HomeworkAPITests(unittest.TestCase):
    def setUp(self):
        self.api = KretaUtils(klik_id='synthetic-school')
        self.network = patch('requests.sessions.Session.request',
                             side_effect=AssertionError('Live requests forbidden in synthetic tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_full_page_range_is_split_without_missing_boundary_days(self):
        # The page requests two weeks behind and thirty days ahead (44 days).
        requested = []
        def get_chunk(key, params):
            self.assertEqual(key, 'homeworks')
            first, last = (date.fromisoformat(params[k]) for k in ('datumTol', 'datumIg'))
            self.assertLessEqual((last - first).days, 21)
            requested.append((first, last))
            return [homework(f'day-{(first + timedelta(days=n)).isoformat()}')
                    for n in range((last - first).days + 1)]
        with patch.object(self.api, '_get', side_effect=get_chunk):
            result = self.api.get_homework('2026-09-09', '2026-10-23')
        self.assertEqual(len(requested), 3)
        self.assertEqual(requested[0][0], date(2026, 9, 9))
        self.assertEqual(requested[-1][1], date(2026, 10, 23))
        self.assertEqual([current[1] for current in requested[:-1]],
                         [following[0] for following in requested[1:]])
        self.assertEqual(len(result), 45)
        self.assertEqual(len({item['id'] for item in result}), 45)

    def test_short_dashboard_range_remains_one_request(self):
        with patch.object(self.api, '_get', return_value=[homework()]) as get:
            result = self.api.get_homework('2026-09-23', '2026-10-07')
        get.assert_called_once_with('homeworks', {'datumTol': '2026-09-23', 'datumIg': '2026-10-07'})
        self.assertEqual(result[0]['deadline'], '2026-09-30')
        self.assertEqual(result[0]['date_added'], '2026-09-23')

    def test_same_day_and_exact_three_week_range_remain_single_requests(self):
        for end in ('2026-09-23', '2026-10-14'):
            with self.subTest(end=end), patch.object(self.api, '_get', return_value=[]) as get:
                self.assertEqual(self.api.get_homework('2026-09-23', end), [])
                self.assertEqual(get.call_count, 1)

    def test_rows_without_ids_are_not_silently_dropped(self):
        with patch.object(self.api, '_get', return_value=[homework(None), homework('')]):
            self.assertEqual(len(self.api.get_homework('2026-09-23', '2026-09-24')), 2)

    def test_invalid_range_is_rejected_before_network(self):
        for first, last in [('2026-10-02', '2026-10-01'), ('invalid', '2026-10-01')]:
            with self.subTest(first=first), patch.object(self.api, '_get') as get:
                with self.assertRaises(ValueError):
                    self.api.get_homework(first, last)
                get.assert_not_called()

    def test_upstream_failure_is_not_disguised_as_an_empty_or_complete_list(self):
        with patch.object(self.api, '_get', side_effect=[[homework()], KretaAPIError('Synthetic failure')]):
            with self.assertRaises(KretaAPIError):
                self.api.get_homework('2026-09-09', '2026-10-23')

    def test_wrong_shape_is_a_safe_api_error(self):
        for payload in ({'Message': 'synthetic-private-marker'}, [None], 'synthetic-private-marker'):
            with self.subTest(payload=type(payload).__name__), patch.object(self.api, '_get', return_value=payload):
                with self.assertRaises(KretaAPIError) as error:
                    self.api.get_homework('2026-09-23', '2026-09-24')
                self.assertNotIn('synthetic-private-marker', str(error.exception))


if __name__ == '__main__':
    unittest.main()
