"""Presentation calculations, kept out of Jinja and independent of authentication."""
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

from date_utils import local_date_string


def today():
    return datetime.now(ZoneInfo('Europe/Budapest')).date()


def school_year(period='all', on=None):
    on = on or today()
    year = on.year - (on.month < 9)
    start, end = date(year, 9, 1), date(year + 1, 8, 31)
    if period == 'first':
        end = date(year + 1, 1, 31)
    elif period == 'second':
        start = date(year + 1, 2, 1)
    return start.isoformat(), end.isoformat()


def number(value, default=0):
    try:
        result = float(value)
        return result if result == result and abs(result) != float('inf') else default
    except (TypeError, ValueError):
        return default


def numeric_grade(grade):
    value = number(grade.get('numeric_value'))
    return int(value) if value in (1, 2, 3, 4, 5) else 0


def average(grades):
    values = [(numeric_grade(g), max(0, number(g.get('weight'), 100))) for g in grades]
    values = [(v, w) for v, w in values if v and w]
    weight = sum(w for _, w in values)
    return round(sum(v * w for v, w in values) / weight, 2) if weight else None


def grade_summary(grades):
    grades = sorted(grades or [], key=lambda g: g.get('date') or '')
    subjects, monthly = defaultdict(list), defaultdict(list)
    for grade in grades:
        subjects[grade.get('subject') or 'Ismeretlen tantárgy'].append(grade)
        if numeric_grade(grade) and len(grade.get('date') or '') >= 7:
            monthly[grade['date'][:7]].append(grade)
    values = [numeric_grade(g) for g in grades if numeric_grade(g)]
    counts = Counter(values)
    distribution = [counts[i] for i in range(1, 6)]
    rows = [{'name': name, 'grades': items, 'average': average(items), 'count': len(items)}
            for name, items in sorted(subjects.items())]
    return {
        'count': len(grades), 'numeric_count': len(values), 'average': average(grades),
        'simple_average': round(sum(values) / len(values), 2) if values else None,
        'best': max(values) if values else None, 'worst': min(values) if values else None,
        'mode': counts.most_common(1)[0][0] if counts else None,
        'distribution': distribution, 'max_count': max(distribution, default=0),
        'subjects': rows, 'latest': list(reversed(grades))[:5],
        'trend': [{'label': month, 'value': average(items)} for month, items in sorted(monthly.items())],
    }


def text_value(value):
    if isinstance(value, dict):
        return value.get('Leiras') or value.get('Nev') or value.get('nev') or ''
    return str(value or '')


def plain_text(value):
    """External message/homework HTML is displayed as text, never executable markup."""
    return BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True)


def absence_rows(items):
    rows = []
    for item in items or []:
        lesson = item.get('Ora') or {}
        status = text_value(item.get('IgazolasAllapota')) or 'Elbírálás alatt'
        rows.append({'subject': text_value(item.get('Tantargy') or lesson.get('Tantargy')) or 'Foglalkozás',
                     'date': local_date_string(item.get('Datum') or lesson.get('KezdetIdopont')),
                     'type': text_value(item.get('Tipus')) or 'Hiányzás', 'status': status,
                     'minutes': item.get('KesesPercben') or 0,
                     'justified': 'igazolt' in status.lower() and 'igazolatlan' not in status.lower()})
    return sorted(rows, key=lambda item: item['date'], reverse=True)


def test_rows(items):
    return sorted([{'subject': text_value(item.get('Tantargy')) or item.get('TantargyNeve') or 'Számonkérés',
                    'topic': item.get('Temaja') or item.get('Tema') or item.get('Nev') or '',
                    'date': local_date_string(item.get('Datum') or item.get('SzamonkeresDatuma')),
                    'teacher': item.get('RogzitoTanarNeve') or item.get('TanarNeve') or '',
                    'type': text_value(item.get('Modja') or item.get('Tipus'))}
                   for item in items or []], key=lambda item: item['date'])
