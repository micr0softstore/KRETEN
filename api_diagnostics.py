"""Allowlisted diagnostics: never format upstream bodies, URLs or exceptions."""
import logging

from kreta_utils import AuthenticationError, KretaAPIError

logger = logging.getLogger('kreten.api')
OPERATIONS = {
    'Házi feladatok': 'HOMEWORK', 'Üzenetek': 'MESSAGES', 'Üzenet': 'MESSAGE',
    'Fogadóórák': 'CONSULTATIONS', 'Tanulói adatok': 'STUDENT', 'Órarend': 'TIMETABLE',
    'Értékelések': 'GRADES', 'Dolgozatok': 'TESTS', 'Tanév rendje': 'SCHOOL_YEAR',
    'Mulasztások': 'ABSENCES', 'LEP események': 'LEP',
    'Feljegyzések': 'SCHOOL_NOTES', 'Faliújság': 'SCHOOL_BOARD',
    'bank_update': 'BANK_UPDATE', 'bank_delete': 'BANK_DELETE',
    'contact_update': 'CONTACT_UPDATE', 'lep_permission': 'LEP_PERMISSION',
}


def report_failure(operation, error):
    operation = OPERATIONS.get(operation, 'UNKNOWN')
    status = None
    if isinstance(error, AuthenticationError):
        category, status = 'API_AUTH', error.status
    elif isinstance(error, KretaAPIError):
        category, status = error.api_code, error.http_status
    elif isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        category = 'API_FORMAT'
    else:
        category = 'CLIENT_ERROR'
    if category not in {'API_AUTH', 'API_FAILED', 'API_HTTP', 'API_NETWORK', 'API_TIMEOUT', 'API_TLS', 'API_FORMAT', 'CLIENT_ERROR'}:
        category = 'CLIENT_ERROR'
    status = status if type(status) is int and 100 <= status <= 599 else None
    logger.warning('KRÉTA request failed: operation=%s category=%s status=%s', operation, category, status or '-')
    code = f'{operation} / {category}' + (f' / HTTP {status}' if status else '')
    if status == 403 and operation in {'BANK_UPDATE', 'BANK_DELETE'}:
        explanation = 'A KRÉTA nem engedélyezte a bankszámla módosítását. Nyisd meg a hivatalos KRÉTA profilodat; a módosításhoz további azonosításra lehet szükség.'
    elif status == 403:
        explanation = 'A KRÉTA ehhez a művelethez nem adott jogosultságot.'
    elif status == 401:
        explanation = 'A munkamenet lejárt. Jelentkezz be újra.'
    elif category == 'API_FORMAT':
        explanation = 'A KRÉTA válaszát nem sikerült feldolgozni.'
    elif category in {'API_NETWORK', 'API_TIMEOUT', 'API_TLS'}:
        explanation = 'A KRÉTA szolgáltatása most nem érhető el. Próbáld újra később.'
    else:
        explanation = 'A kérés nem sikerült.'
    return f'{explanation} (Hibakód: {code})'
