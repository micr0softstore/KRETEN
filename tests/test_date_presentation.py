"""Synthetic local-school-date regressions, including midnight and DST changes."""
from datetime import date
import unittest
from unittest.mock import patch

from date_utils import local_datetime, local_date_string
from kreta_utils import KretaUtils
from view_models import absence_rows, test_rows as exam_rows


class SchoolDatePresentationTests(unittest.TestCase):
    def setUp(self):
        self.api = KretaUtils(klik_id='synthetic-school')
        self.network = patch('requests.sessions.Session.request',
                             side_effect=AssertionError('Live requests forbidden in synthetic tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_midnight_homework_exams_and_absences_share_budapest_dates(self):
        cases = [
            ('2026-09-28T22:00:00Z', '2026-09-29'),
            ('2026-12-14T23:00:00Z', '2026-12-15'),
            ('2026-03-28T23:00:00Z', '2026-03-29'),
            ('2026-03-29T22:00:00Z', '2026-03-30'),
            ('2026-10-24T22:00:00Z', '2026-10-25'),
            ('2026-10-25T23:00:00Z', '2026-10-26'),
        ]
        for timestamp, expected in cases:
            with self.subTest(timestamp=timestamp):
                self.assertEqual(exam_rows([{'Datum': timestamp}])[0]['date'], expected)
                self.assertEqual(absence_rows([{'Datum': timestamp}])[0]['date'], expected)
                homework = self.api.convert_homework([{'HataridoDatuma': timestamp, 'RogzitesIdopontja': timestamp}])[0]
                self.assertEqual(homework['deadline'], expected)
                self.assertEqual(homework['date_added'], expected)

    def test_grade_date_and_metadata_keep_existing_adapter_behavior(self):
        for value in ('2026-09-28T22:00:00Z', '2026-09-29'):
            with self.subTest(value=value):
                grade = self.api.convert_grades([{
                    'Uid': 'synthetic-grade', 'KeszitesDatuma': value,
                    'SzamErtek': 5, 'SulySzazalekErteke': 200,
                    'Tantargy': {'Nev': 'Synthetic subject'},
                    'Tema': 'Synthetic topic', 'ErtekeloTanarNeve': 'Synthetic teacher',
                }])[0]
                self.assertEqual(grade['date'], '2026-09-29')
                self.assertEqual(grade['numeric_value'], 5)
                self.assertEqual(grade['weight'], 200)
                self.assertEqual(grade['subject'], 'Synthetic subject')
                self.assertEqual(grade['topic'], 'Synthetic topic')
                self.assertEqual(grade['teacher'], 'Synthetic teacher')

    def test_explicit_offsets_can_cross_either_calendar_boundary(self):
        self.assertEqual(local_date_string('2026-09-29T00:00:00+02:00'), '2026-09-29')
        self.assertEqual(local_date_string('2026-09-29T01:00:00+05:00'), '2026-09-28')
        self.assertEqual(local_date_string('2026-09-28T21:00:00-02:00'), '2026-09-29')

    def test_calendar_dates_and_naive_local_datetimes_do_not_shift(self):
        for value in ['2026-09-29', '2026-03-29', '2026-10-25', '2026-09-29T00:00:00', date(2026, 9, 29)]:
            with self.subTest(value=value):
                expected = str(value)[:10]
                self.assertEqual(local_date_string(value), expected)
                self.assertEqual(exam_rows([{'Datum': value}])[0]['date'], expected)
                self.assertEqual(absence_rows([{'Datum': value}])[0]['date'], expected)
                self.assertEqual(self.api.convert_homework([{'HataridoDatuma': value}])[0]['deadline'], expected)

    def test_alternate_exam_and_lesson_date_fields_are_localized(self):
        timestamp = '2026-09-28T22:00:00Z'
        self.assertEqual(exam_rows([{'SzamonkeresDatuma': timestamp}])[0]['date'], '2026-09-29')
        self.assertEqual(absence_rows([{'Ora': {'KezdetIdopont': timestamp}}])[0]['date'], '2026-09-29')

    def test_exam_and_absence_sorting_uses_displayed_local_day(self):
        records = [{'Datum': '2026-09-28T22:00:00Z', 'Tantargy': {'Nev': 'Later'}},
                   {'Datum': '2026-09-29T01:00:00+05:00', 'Tantargy': {'Nev': 'Earlier'}}]
        self.assertEqual([item['subject'] for item in exam_rows(records)], ['Earlier', 'Later'])
        self.assertEqual([item['subject'] for item in absence_rows(records)], ['Later', 'Earlier'])

    def test_invalid_or_missing_dates_do_not_invent_calendar_days(self):
        for value in [None, '', 'invalid', '2026-02-30', '2026-09-29malformed', {}]:
            with self.subTest(value=value):
                self.assertEqual(local_date_string(value), '')
                self.assertEqual(exam_rows([{'Datum': value}])[0]['date'], '')
                self.assertEqual(absence_rows([{'Datum': value}])[0]['date'], '')

    def test_dst_conversion_changes_offset_without_losing_wall_clock_time(self):
        before = local_datetime('2026-03-29T00:30:00Z')
        after = local_datetime('2026-03-29T01:30:00Z')
        self.assertEqual(before.strftime('%Y-%m-%d %H:%M %z'), '2026-03-29 01:30 +0100')
        self.assertEqual(after.strftime('%Y-%m-%d %H:%M %z'), '2026-03-29 03:30 +0200')
        # The repeated autumn hour has two distinct UTC offsets.
        before = local_datetime('2026-10-25T00:30:00Z')
        after = local_datetime('2026-10-25T01:30:00Z')
        self.assertEqual(before.strftime('%H:%M %z'), '02:30 +0200')
        self.assertEqual(after.strftime('%H:%M %z'), '02:30 +0100')


if __name__ == '__main__':
    unittest.main()
