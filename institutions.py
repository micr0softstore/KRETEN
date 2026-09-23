"""Cached public KRÉTA institution directory, independent of user sessions."""
import re
import threading
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

DIRECTORY_URL = 'https://kretaglobalapi.e-kreta.hu/intezmenyek/kreta/publikus'
SEARCH_URL = 'https://intezmenykereso.e-kreta.hu/instituteSelector/'
FALLBACK_INSTITUTIONS = [{
    'name': 'Biatorbágyi Innovatív Technikum és Gimnázium',
    'code': 'bit-edu', 'id': '910018', 'city': 'Biatorbágy',
    'label': 'Biatorbágyi Innovatív Technikum és Gimnázium (bit-edu · 910018)',
}]


def validate_institution_code(value):
    value = (value or '').strip().lower()
    # Institution codes form exactly one DNS label, never an arbitrary URL.
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
        raise ValueError('Érvénytelen intézménykód. Válassz iskolát, vagy írd be az intézmény kódját.')
    return value


def normalize_institutions(payload):
    if isinstance(payload, dict):
        payload = payload.get('institutes', payload.get('Institutes', payload.get('items', [])))
    if not isinstance(payload, list):
        raise ValueError('Érvénytelen intézménylista.')
    institutions = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            code = validate_institution_code(item.get('instituteCode', item.get('InstituteCode', item.get('code'))))
        except (ValueError, AttributeError):
            continue
        name = item.get('name', item.get('Name', code)) or code
        identifier = str(item.get('instituteId', item.get('InstituteId', item.get('id', ''))) or '')
        city = item.get('city', item.get('City', '')) or ''
        institutions[code] = {'name': name, 'code': code, 'id': identifier, 'city': city,
                              'label': f'{name} ({code}' + (f' · {identifier}' if identifier else '') + ')'}
    if not institutions:
        raise ValueError('Üres intézménylista.')
    return sorted(institutions.values(), key=lambda row: row['name'].casefold())


def normalize_search_results(html):
    rows = {}
    for element in BeautifulSoup(html, 'html.parser').select('a[data-val]'):
        try:
            code = validate_institution_code(element.get('data-val'))
        except ValueError:
            continue
        label = element.get_text(' ', strip=True)
        identifier = re.search(r'(?:-|·)\s*(\d+)\)\s*$', label)
        name = label.rsplit('(', 1)[0].strip() if '(' in label else label
        rows[code] = {'name': name or code, 'code': code, 'id': identifier.group(1) if identifier else '',
                      'city': '', 'label': label or code}
    return sorted(rows.values(), key=lambda row: row['name'].casefold())


def _search_text(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))


class InstitutionDirectory:
    def __init__(self, ttl=24 * 60 * 60):
        self.ttl = ttl
        self._institutions = None
        self._expires_at = 0
        self._lock = threading.Lock()
        self._source = 'fallback'
        self._search_cache = {}

    def _search(self, query):
        # This official school selector is independently available when the
        # complete public mobile directory is down. It returns HTML fragments.
        key = _search_text(query)
        with self._lock:
            cached = self._search_cache.get(key)
            if cached and cached['expires_at'] > time.time():
                return cached['rows']
            try:
                response = requests.get(SEARCH_URL + quote(query, safe=''), params={'showOnlyLive': 'true'},
                                        timeout=(4, 10), headers={'User-Agent': 'KRETEN-Web/1.0'})
                response.raise_for_status()
                rows = normalize_search_results(response.text)
                if len(self._search_cache) >= 128:
                    self._search_cache.pop(next(iter(self._search_cache)))
                self._search_cache[key] = {'rows': rows, 'expires_at': time.time() + 3600}
                return rows
            except (requests.RequestException, ValueError, TypeError):
                return None

    def get(self, query=None):
        """Return {institutions, source, stale}; failures retain the last good list."""
        if time.time() >= self._expires_at:
            with self._lock:
                if time.time() >= self._expires_at:
                    try:
                        response = requests.get(DIRECTORY_URL, timeout=(4, 10),
                                                headers={'Accept': 'application/json', 'User-Agent': 'KRETEN-Web/1.0'})
                        response.raise_for_status()
                        self._institutions = normalize_institutions(response.json())
                        self._expires_at = time.time() + self.ttl
                        self._source = 'kreta'
                    except (requests.RequestException, ValueError, TypeError):
                        self._expires_at = time.time() + 60
                        self._source = 'cached' if self._institutions else 'fallback'
        query = (query or '').strip()[:150]
        if not self._institutions and len(query) >= 2:
            searched = self._search(query)
            if searched is not None:
                return {'institutions': [dict(row) for row in searched], 'source': 'kreta-search', 'stale': False}
        rows = self._institutions or FALLBACK_INSTITUTIONS
        if query:
            needle = _search_text(query)
            rows = [row for row in rows if needle in _search_text(row['label'] + ' ' + row['city'])]
        return {'institutions': [dict(row) for row in rows], 'source': self._source,
                'stale': self._source != 'kreta'}
