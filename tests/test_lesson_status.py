import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lesson_status import current_status, lesson_intervals, lesson_status_context


def lesson(start='08:00', end='08:45', day='2026-09-29', **fields):
    return {'date': day, 'start_time': start, 'end_time': end,
            'subject': 'Matematika', 'teacher': 'Minta Tanár', **fields}


def epoch(time, day='2026-09-29'):
    return int(datetime.fromisoformat(f'{day}T{time}').replace(tzinfo=ZoneInfo('Europe/Budapest')).timestamp() * 1000)


class LessonStatusTests(unittest.TestCase):
    def setUp(self):
        self.intervals = lesson_intervals([lesson(), lesson('09:00', '09:45', subject='Irodalom'),
                                           lesson(day='2026-09-30')])

    def status(self, time, day='2026-09-29'):
        return current_status(self.intervals, epoch(time, day))

    def test_budapest_epochs_in_summer_and_winter(self):
        summer = lesson_intervals([lesson()])[0]
        winter = lesson_intervals([lesson(day='2026-12-01')])[0]
        self.assertEqual(datetime.fromtimestamp(summer['start'] / 1000, timezone.utc).hour, 6)
        self.assertEqual(datetime.fromtimestamp(winter['start'] / 1000, timezone.utc).hour, 7)

    def test_lesson_break_next_lesson_exact_boundaries(self):
        self.assertEqual(self.status('07:59:59')['kind'], 'upcoming')
        beginning = self.status('08:00:00')
        self.assertEqual((beginning['kind'], beginning['progress']), ('lesson', 0))
        self.assertEqual(beginning['remaining'], '45 perc 00 mp van hátra')
        self.assertEqual(self.status('08:44:59')['remaining'], '0 perc 01 mp van hátra')
        break_start = self.status('08:45:00')
        self.assertEqual((break_start['kind'], break_start['progress']), ('break', 0))
        self.assertEqual(break_start['remaining'], '15 perc 00 mp van hátra')
        self.assertEqual(self.status('08:52:30')['progress'], 50)
        self.assertEqual(self.status('09:00:00')['title'], 'Irodalom')
        self.assertEqual(self.status('09:00:00')['kind'], 'lesson')

    def test_no_false_overnight_break_or_negative_timer(self):
        after_school = self.status('09:45:00')
        self.assertEqual(after_school['kind'], 'upcoming')
        self.assertEqual(after_school['date'], '2026-09-30')
        self.assertEqual(after_school['remaining'], '')
        self.assertEqual(self.status('07:00', '2026-09-30')['kind'], 'upcoming')
        end = self.status('08:45', '2026-09-30')
        self.assertEqual((end['kind'], end['remaining']), ('empty', ''))

    def test_cancelled_and_empty_periods_do_not_become_active(self):
        intervals = lesson_intervals([lesson(), lesson('09:00', '09:45', state='Elmaradt'),
                                     lesson('10:00', '10:45', type='UresOra'),
                                     lesson('11:00', '11:45', type='ElmaradtOra'),
                                     lesson('12:00', '12:45', subject='Angol')])
        self.assertEqual(len(intervals), 2)
        during_cancelled = current_status(intervals, epoch('09:15'))
        self.assertEqual(during_cancelled['kind'], 'break')
        self.assertIn('Angol', during_cancelled['details'])

    def test_invalid_intervals_and_empty_schedule(self):
        intervals = lesson_intervals([{}, lesson(end='08:00'), lesson(end='07:45'),
                                     lesson(start='bad'), lesson(day='bad')])
        self.assertEqual(intervals, [])
        self.assertEqual(current_status(intervals, epoch('09:00'))['kind'], 'empty')

    def test_overlapping_lesson_is_not_reported_as_break(self):
        intervals = lesson_intervals([lesson('08:30', '09:00', subject='Angol'), lesson()])
        self.assertEqual(current_status(intervals, epoch('08:50'))['kind'], 'lesson')

    def test_server_context_accepts_utc_and_picks_same_school_time(self):
        context = lesson_status_context([lesson()], datetime(2026, 9, 29, 6, 20, tzinfo=timezone.utc))
        self.assertEqual(context['lesson_status']['kind'], 'lesson')
        self.assertEqual(context['lesson_status']['remaining'], '25 perc 00 mp van hátra')
        self.assertEqual(context['lesson_clock']['server_now'], epoch('08:20'))


if __name__ == '__main__':
    unittest.main()
