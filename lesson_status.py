"""Current lesson and real between-lesson breaks, using Budapest school time."""
from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

BUDAPEST = ZoneInfo('Europe/Budapest')


def lesson_intervals(lessons):
    intervals = []
    for lesson in lessons:
        state = str(lesson.get('state') or '').casefold()
        if 'elmarad' in state or lesson.get('type') in ('ElmaradtOra', 'UresOra'):
            continue
        try:
            day = str(lesson.get('date') or '')
            start = datetime.fromisoformat(f"{day}T{lesson['start_time']}")
            end = datetime.fromisoformat(f"{day}T{lesson['end_time']}")
            if start.tzinfo is None:
                start = start.replace(tzinfo=BUDAPEST)
            if end.tzinfo is None:
                end = end.replace(tzinfo=BUDAPEST)
            start = start.astimezone(BUDAPEST)
            end = end.astimezone(BUDAPEST)
            if end <= start or start.date() != end.date():
                continue
        except (KeyError, TypeError, ValueError):
            continue
        intervals.append({
            'start': int(start.timestamp() * 1000), 'end': int(end.timestamp() * 1000),
            'date': start.date().isoformat(),
            'subject': str(lesson.get('subject') or lesson.get('name') or 'Tanóra'),
            'time': f"{start:%H:%M} – {end:%H:%M}",
            'classroom': str(lesson.get('classroom') or 'Terem nincs megadva'),
            'teacher': str(lesson.get('substitute_teacher') or lesson.get('teacher') or ''),
        })
    return sorted(intervals, key=lambda item: (item['start'], item['end']))


def remaining_text(milliseconds):
    seconds = max(0, ceil(milliseconds / 1000))
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes} perc {seconds:02} mp van hátra'


def current_status(intervals, now_ms):
    active = next((item for item in intervals if item['start'] <= now_ms < item['end']), None)
    upcoming = next((item for item in intervals if item['start'] > now_ms), None)
    previous = max((item for item in intervals if item['end'] <= now_ms),
                   key=lambda item: item['end'], default=None)
    status = {'kind': 'empty', 'label': 'SZABAD IDŐ', 'date': '',
              'title': 'Nincs közelgő tanóra.',
              'details': 'Nincs több közelgő óra a következő hétre.', 'teacher': '',
              'remaining': '', 'progress': 0, 'progress_label': ''}
    if active:
        status.update(kind='lesson', label='MOST ÓRÁD VAN', title=active['subject'],
                      date=active['date'], details=f"{active['time']} / {active['classroom']}",
                      teacher=active['teacher'], progress_label='Az óra eltelt része')
        start, end = active['start'], active['end']
    elif upcoming and previous and previous['date'] == upcoming['date']:
        status.update(kind='break', label='KÉT ÓRA KÖZÖTT', title='Szünet',
                      date=upcoming['date'], details=f"Következik: {upcoming['subject']} · {upcoming['time']}",
                      teacher=upcoming['classroom'], progress_label='A szünet eltelt része')
        start, end = previous['end'], upcoming['start']
    elif upcoming:
        status.update(kind='upcoming', label='A KÖVETKEZŐ ÓRÁD', title=upcoming['subject'],
                      date=upcoming['date'], details=f"{upcoming['time']} / {upcoming['classroom']}",
                      teacher=upcoming['teacher'])
        return status
    else:
        return status
    status['remaining'] = remaining_text(end - now_ms)
    status['progress'] = round(min(100, max(0, (now_ms - start) / (end - start) * 100)))
    return status


def lesson_status_context(lessons, now=None):
    now = now or datetime.now(BUDAPEST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BUDAPEST)
    now_ms = int(now.timestamp() * 1000)
    intervals = lesson_intervals(lessons)
    return {'lesson_clock': {'server_now': now_ms, 'lessons': intervals},
            'lesson_status': current_status(intervals, now_ms)}
