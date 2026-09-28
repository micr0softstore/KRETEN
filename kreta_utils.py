"""KRÉTA API adapter. Authentication material is instance-scoped and never logged."""
from base64 import urlsafe_b64encode
from datetime import date, timedelta
from hashlib import sha256
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlparse, urljoin, quote

from bs4 import BeautifulSoup
import requests

from date_utils import local_datetime
from institutions import validate_institution_code
from session_store import clean_tokens


class KretaAPIError(Exception):
    """A safe-to-display upstream API error, without response bodies or secrets."""

    def __init__(self, message, *, api_code='API_FAILED', status=None):
        self.api_code = api_code if api_code in {'API_FAILED', 'API_HTTP', 'API_NETWORK', 'API_TIMEOUT', 'API_TLS', 'API_FORMAT'} else 'API_FAILED'
        self.http_status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(message)


class AuthenticationError(KretaAPIError):
    # Only predefined categories reach the browser, never exception text, OAuth
    # URLs, form values, response bodies, cookies, or tokens.
    MESSAGES = {
        'AUTH_FAILED': 'Nem sikerült bejelentkezni a KRÉTA rendszerébe.',
        'AUTH_INPUT': 'Add meg a felhasználóneved, a jelszavad és a megfelelő intézményt.',
        'AUTH_PAGE': 'A KRÉTA bejelentkezési oldala nem töltődött be megfelelően.',
        'AUTH_FORM': 'A KRÉTA belépési űrlapja nem fogadta el a kérést. Ellenőrizd a belépési adatokat és az intézményt.',
        'AUTH_CHECK': 'A KRÉTA további böngészős ellenőrzést kér. Jelentkezz be a hivatalos KRÉTA felületén; ezt az ellenőrzést a kliens nem tudja elvégezni.',
        'AUTH_REDIRECT': 'A KRÉTA nem fejezte be a bejelentkezési átirányítást.',
        'AUTH_STATE': 'A KRÉTA bejelentkezési válaszának biztonsági ellenőrzése nem sikerült. Próbáld újra.',
        'AUTH_TOKEN': 'A KRÉTA bejelentkezése után nem sikerült hozzáférési tokent kérni.',
        'AUTH_NETWORK': 'A szerver nem tud kapcsolódni a KRÉTA bejelentkezési szolgáltatásához. Ez nem jelenti azt, hogy hibás a jelszavad.',
        'AUTH_TIMEOUT': 'A KRÉTA bejelentkezési szolgáltatása nem válaszolt időben. Próbáld újra később.',
        'AUTH_TLS': 'A szerver nem tud biztonságos kapcsolatot létrehozni a KRÉTA bejelentkezési szolgáltatásával.',
    }

    def __init__(self, message=None, *, code='AUTH_FAILED', status=None):
        self.code = code if code in self.MESSAGES else 'AUTH_FAILED'
        self.status = status if isinstance(status, int) and 100 <= status <= 599 else None
        super().__init__(message or self.MESSAGES[self.code])

    @property
    def public_message(self):
        identifier = self.code + (f' / HTTP {self.status}' if self.status else '')
        return f'{self.MESSAGES[self.code]} (Hibakód: {identifier})'


class KretaUtils:
    CLIENT_ID = 'kreta-ellenorzo-student-mobile-ios'
    REDIRECT_URI = 'https://mobil.e-kreta.hu/ellenorzo-student/prod/oauthredirect'
    TIMEOUT = (5, 20)
    LOGIN_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

    def __init__(self, user_name=None, password=None, klik_id=None):
        # Construction restores no account and makes no network requests. Password
        # is accepted for old call sites, but is never retained on the object.
        self.user_name = user_name
        self.klik_id = validate_institution_code(klik_id)
        self.base_url = f'https://{self.klik_id}.e-kreta.hu'
        self.idp_url = 'https://idp.e-kreta.hu'
        self.admin_url = 'https://eugyintezes.e-kreta.hu'
        self.files_url = 'https://files.e-kreta.hu'
        self.access_token = None
        self.token_data = {}
        self.refresh_handler = None
        self.headers = {'User-Agent': 'hu.ekreta.student/1.0.5/Android/0/0',
                        'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
        self.request_headers = self.headers.copy()
        self.endpoints = {
            'notes': '/ellenorzo/v3/Sajat/Feljegyzesek',
            'events': '/ellenorzo/v3/Sajat/FaliujsagElemek',
            'student': '/ellenorzo/v3/Sajat/TanuloAdatlap',
            'evaluations': '/ellenorzo/v3/Sajat/Ertekelesek',
            'absences': '/ellenorzo/v3/Sajat/Mulasztasok',
            'groups': '/ellenorzo/v3/Sajat/OsztalyCsoportok',
            'classAverages': '/ellenorzo/v3/Sajat/Ertekelesek/Atlagok/OsztalyAtlagok',
            'timetable': '/ellenorzo/v3/Sajat/OrarendElemek',
            'announcedTests': '/ellenorzo/v3/Sajat/BejelentettSzamonkeresek',
            'homeworks': '/ellenorzo/v3/Sajat/HaziFeladatok',
            'homeworkDone': '/ellenorzo/v3/Sajat/HaziFeladatok/Megoldva',
            'capabilities': '/ellenorzo/v3/Sajat/Intezmenyek',
            'messages': '/api/v1/kommunikacio/postaladaelemek',
            'message_details': '/api/v1/kommunikacio/postaladaelemek/{id}',
            'guardian': '/ellenorzo/v3/Sajat/GondviseloAdatlap',
            'device_state': '/ellenorzo/v3/TargyiEszkoz/IsEszkozKiosztva',
            'registration_state': '/ellenorzo/v3/TargyiEszkoz/IsRegisztralt',
            'bank_account': '/ellenorzo/v3/Sajat/Bankszamla',
            'contact': '/ellenorzo/v3/Sajat/Elerhetoseg',
            'covid_form': '/ellenorzo/v3/Bejelentes/Covid',
            'lep_events': '/ellenorzo/v3/Lep/Eloadasok',
            'class_masters': '/ellenorzo/v3/Felhasznalok/Alkalmazottak/Tanarok/Osztalyfonokok',
            'consulting_hours': '/ellenorzo/v3/Sajat/Fogadoorak',
            'timetable_weeks': '/ellenorzo/v3/Sajat/Intezmenyek/Hetirendek/Orarendi',
        }

    @classmethod
    def from_tokens(cls, username, institute_code, token_data, refresh_handler=None):
        client = cls(username, None, institute_code)
        client._set_tokens(token_data)
        client.refresh_handler = refresh_handler
        return client

    def _set_tokens(self, token_data):
        self.token_data = clean_tokens(token_data)
        self.access_token = self.token_data['access_token']
        self.request_headers['Authorization'] = f'Bearer {self.access_token}'
        return dict(self.token_data)

    def login(self, user_name, password, klik_id):
        """Keep the existing IDP form -> OAuth code -> token flow with fresh PKCE."""
        code = validate_institution_code(klik_id)
        if code != self.klik_id:
            raise AuthenticationError(code='AUTH_INPUT')
        if not user_name or not password:
            raise AuthenticationError(code='AUTH_INPUT')
        verifier = secrets.token_urlsafe(48)
        challenge = urlsafe_b64encode(sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
        state = secrets.token_urlsafe(24)
        parameters = {
            'prompt': 'login', 'nonce': secrets.token_urlsafe(24), 'response_type': 'code',
            'code_challenge_method': 'S256',
            'scope': ('openid email offline_access kreta-ellenorzo-webapi.public '
                      'kreta-eugyintezes-webapi.public kreta-fileservice-webapi.public '
                      'kreta-mobile-global-webapi.public kreta-dkt-webapi.public kreta-ier-webapi.public'),
            'code_challenge': challenge, 'redirect_uri': self.REDIRECT_URI,
            'client_id': self.CLIENT_ID, 'state': state, 'suppressed_prompt': 'login',
        }
        # Keep the working client's percent-encoded spaces in the nested return
        # URL; some IDP processing differs for form-style '+' encoding.
        callback = '/connect/authorize/callback?' + urlencode(parameters, quote_via=quote)
        stage = 'AUTH_PAGE'
        try:
            with requests.Session() as http:
                # Keep the original form/cookie flow: default client headers on
                # the initial GET, then the original browser header on POST.
                response = http.get(self.idp_url + '/Account/Login', params={'ReturnUrl': callback},
                                    allow_redirects=False, timeout=self.TIMEOUT)
                response.raise_for_status()
                page = BeautifulSoup(response.text, 'html.parser')
                verification = page.find('input', {'name': '__RequestVerificationToken'})
                if not verification or not verification.get('value'):
                    raise AuthenticationError(code='AUTH_PAGE', status=response.status_code)
                stage = 'AUTH_FORM'
                response = http.post(self.idp_url + '/account/login', data={
                    'ReturnUrl': callback, 'IsTemporaryLogin': False, 'UserName': user_name,
                    'Password': password, 'InstituteCode': code, 'loginType': 'InstituteLogin',
                    '__RequestVerificationToken': verification['value'],
                }, headers={'User-Agent': self.LOGIN_USER_AGENT, 'Content-Type': 'application/x-www-form-urlencoded'},
                    allow_redirects=False, timeout=self.TIMEOUT)
                response.raise_for_status()
                if response.status_code == 200:
                    rejected = BeautifulSoup(response.text, 'html.parser')
                    check = rejected.select_one('.g-recaptcha, [name="g-recaptcha-response"], [name="otp"], [autocomplete="one-time-code"]')
                    if check:
                        raise AuthenticationError(code='AUTH_CHECK', status=200)
                form_returned_page = response.status_code == 200
                stage = 'AUTH_REDIRECT'
                try:
                    # The original client always continued here, even for a
                    # 200 form response: the cookie jar may now be signed in.
                    redirect = self._login_redirect(http, response, callback)
                except AuthenticationError as error:
                    if form_returned_page and error.code == 'AUTH_REDIRECT' and error.status == 200:
                        raise AuthenticationError(code='AUTH_FORM', status=200) from None
                    raise
                result = parse_qs(redirect.query)
                if result.get('state') != [state]:
                    raise AuthenticationError(code='AUTH_STATE')
                if len(result.get('code', [])) != 1 or result.get('error'):
                    raise AuthenticationError(code='AUTH_REDIRECT')
                stage = 'AUTH_TOKEN'
                response = http.post(self.idp_url + '/connect/token', data={
                    'code': result['code'][0], 'code_verifier': verifier, 'redirect_uri': self.REDIRECT_URI,
                    'client_id': self.CLIENT_ID, 'grant_type': 'authorization_code',
                }, allow_redirects=False, timeout=self.TIMEOUT)
                response.raise_for_status()
                if not 200 <= response.status_code < 300:
                    raise AuthenticationError(code='AUTH_TOKEN', status=response.status_code)
                return self._set_tokens(response.json())
        except AuthenticationError:
            raise
        except requests.exceptions.SSLError:
            raise AuthenticationError(code='AUTH_TLS') from None
        except requests.Timeout:
            raise AuthenticationError(code='AUTH_TIMEOUT') from None
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else response.status_code
            raise AuthenticationError(code=stage, status=status) from None
        except requests.exceptions.JSONDecodeError:
            raise AuthenticationError(code=stage) from None
        except requests.RequestException:
            raise AuthenticationError(code='AUTH_NETWORK') from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuthenticationError(code=stage) from None

    def _login_redirect(self, http, response, callback):
        """Follow only IDP redirects; never send the OAuth code to another host."""
        location = response.headers.get('Location') or callback
        current_url = self.idp_url + '/account/login'
        expected = urlparse(self.REDIRECT_URI)
        visited = set()
        for _ in range(6):
            target = urljoin(current_url, location)
            parsed = urlparse(target)
            if (parsed.scheme, parsed.netloc, parsed.path) == (expected.scheme, expected.netloc, expected.path):
                return parsed
            if parsed.scheme != 'https' or parsed.netloc != 'idp.e-kreta.hu' or target in visited:
                raise AuthenticationError(code='AUTH_REDIRECT')
            visited.add(target)
            response = http.get(target, allow_redirects=False, timeout=self.TIMEOUT)
            response.raise_for_status()
            if response.status_code not in (301, 302, 303, 307, 308) or not response.headers.get('Location'):
                raise AuthenticationError(code='AUTH_REDIRECT', status=response.status_code)
            current_url, location = target, response.headers['Location']
        raise AuthenticationError(code='AUTH_REDIRECT')

    @classmethod
    def refresh_token_data(cls, institute_code, token_data):
        code = validate_institution_code(institute_code)
        refresh_token = token_data.get('refresh_token')
        if not refresh_token:
            raise AuthenticationError('A munkamenet lejárt. Jelentkezz be újra.')
        try:
            response = requests.post('https://idp.e-kreta.hu/connect/token', data={
                'refresh_token': refresh_token, 'institute_code': code,
                'client_id': cls.CLIENT_ID, 'grant_type': 'refresh_token',
            }, allow_redirects=False, timeout=cls.TIMEOUT)
            response.raise_for_status()
            if not 200 <= response.status_code < 300:
                raise AuthenticationError(code='AUTH_TOKEN', status=response.status_code)
            result = response.json()
            if not result.get('refresh_token'):
                result['refresh_token'] = refresh_token
            return clean_tokens(result)
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise AuthenticationError('A munkamenet lejárt. Jelentkezz be újra.') from None

    def refresh_tokens(self):
        if self.refresh_handler:
            tokens = self.refresh_handler(self.access_token)
        else:
            tokens = self.refresh_token_data(self.klik_id, self.token_data)
        if not tokens:
            raise AuthenticationError('A munkamenet lejárt. Jelentkezz be újra.')
        return self._set_tokens(tokens)

    def _make_request(self, method, endpoint, data=None, headers=None, is_json=False, *, params=None, base_url=None):
        url = (base_url or self.base_url).rstrip('/') + '/' + endpoint.lstrip('/')
        for attempt in range(2):
            request_headers = self.request_headers.copy()
            request_headers.update(headers or {})
            if base_url == self.admin_url:
                # The e-administration service resets connections carrying the
                # mobile diary client's User-Agent. Its own client uses ordinary
                # HTTP headers; keep the mobile identifier on diary calls only.
                request_headers.pop('User-Agent', None)
                request_headers['X-Uzenet-Lokalizacio'] = 'hu-HU'
            if is_json:
                request_headers['Content-Type'] = 'application/json'
            # Never reuse a stale Authorization value passed by a caller.
            request_headers['Authorization'] = f'Bearer {self.access_token}'
            try:
                response = requests.request(method, url, headers=request_headers, params=params,
                                            timeout=self.TIMEOUT, allow_redirects=False,
                                            **({'json': data} if is_json else {'data': data}))
            except requests.exceptions.SSLError:
                raise KretaAPIError('A biztonságos kapcsolat nem jött létre.', api_code='API_TLS') from None
            except requests.Timeout:
                raise KretaAPIError('A KRÉTA nem válaszolt időben.', api_code='API_TIMEOUT') from None
            except requests.RequestException:
                raise KretaAPIError('A KRÉTA szolgáltatása jelenleg nem érhető el.', api_code='API_NETWORK') from None
            if response.status_code == 401:
                if attempt == 0 and (self.refresh_handler or self.token_data.get('refresh_token')):
                    self.refresh_tokens()
                    continue
                raise AuthenticationError('A munkamenet lejárt. Jelentkezz be újra.', status=401)
            if not 200 <= response.status_code < 300:
                raise KretaAPIError('A KRÉTA nem tudta teljesíteni a kérést.', api_code='API_HTTP', status=response.status_code)
            return response

    def _get(self, key, params=None, *, base_url=None, suffix=''):
        response = self._make_request('GET', self.endpoints.get(key, key) + suffix, params=params, base_url=base_url)
        try:
            return response.json()
        except ValueError:
            raise KretaAPIError('A KRÉTA érvénytelen választ küldött.', api_code='API_FORMAT') from None

    def _change(self, method, key, data=None, suffix=''):
        response = self._make_request(method, self.endpoints[key] + suffix, data=data, is_json=True)
        return response.text

    def get_school_year_dates(self):
        return self.convert_school_year_dates(self._get('/ellenorzo/v3/sajat/Intezmenyek/TanevRendjeElemek'))

    def get_student_data(self):
        return self.convert_student_data(self._get('student'))

    def get_homework(self, start_date, end_date):
        # KRÉTA rejects bounded homework queries spanning more than three weeks.
        # Keep the page's complete range by joining small requests. Overlap the
        # boundary day so either inclusive or exclusive end-date handling is
        # covered, and discard repeated Uids from those overlapping boundaries.
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError('A kezdő dátum nem lehet későbbi a záró dátumnál.')
        items, seen = [], set()
        while True:
            chunk_end = min(start + timedelta(days=21), end)
            chunk = self._get('homeworks', {'datumTol': start.isoformat(), 'datumIg': chunk_end.isoformat()})
            if not isinstance(chunk, list) or any(not isinstance(item, dict) for item in chunk):
                raise KretaAPIError('A KRÉTA érvénytelen házifeladat-listát küldött.', api_code='API_FORMAT')
            for item in chunk:
                uid = item.get('Uid')
                if uid is not None and str(uid):
                    uid = str(uid)
                    if uid in seen:
                        continue
                    seen.add(uid)
                items.append(item)
            if chunk_end == end:
                break
            start = chunk_end
        return self.convert_homework(items)

    def get_lessons(self, start_date, end_date):
        return self.convert_lessons(self._get('timetable', {'datumTol': start_date, 'datumIg': end_date}))

    def get_grades(self, start_date, end_date):
        return self.convert_grades(self._get('evaluations', {'datumTol': start_date, 'datumIg': end_date}))

    def get_absences(self):
        return self._get('absences')

    def get_announced_tests(self, date=None):
        return self._get('announcedTests', {'datumTol': date} if date else None)

    def get_school_notices(self, source='notes', start_date=None):
        if source not in ('notes', 'board'):
            raise ValueError('Érvénytelen iskolai üzenetforrás.')
        params = {'datumTol': date.fromisoformat(start_date).isoformat()} if start_date else None
        key = 'notes' if source == 'notes' else 'events'
        return self.convert_school_notices(self._get(key, params), source)

    def convert_school_notices(self, json_data, source):
        """Normalize Firka's InfoBoard/NoticeBoard models for escaped web cards."""
        if source not in ('notes', 'board'):
            raise ValueError('Érvénytelen iskolai üzenetforrás.')
        if not isinstance(json_data, list) or any(not isinstance(item, dict) for item in json_data):
            raise KretaAPIError('A KRÉTA iskolai üzenetlistája érvénytelen.', api_code='API_FORMAT')
        notices = []
        for item in json_data:
            if source == 'notes':
                date_value = item.get('Datum') or item.get('KeszitesDatuma')
                author = item.get('KeszitoTanarNeve')
                content = item.get('Tartalom') or item.get('TartalomFormazott') or ''
                type_label = self._label(item.get('Tipus'), 'Leiras') or self._label(item.get('Tipus'))
            else:
                date_value = item.get('ErvenyessegKezdete')
                author = item.get('RogzitoNeve')
                content = item.get('TartalomText') or item.get('Tartalom') or ''
                type_label = ''
            date_value = self._datetime(date_value)
            notices.append({
                'id': str(item.get('Uid') or ''),
                'title': str(item.get('Cim') or 'Nincs cím'),
                'author': str(author or ''),
                'date': date_value.strftime('%Y-%m-%d %H:%M') if date_value else '',
                # Prefer the API's text version. Templates strip any remaining
                # markup with plain_text and retain Jinja's automatic escaping.
                'text': str(content),
                'source_label': 'Feljegyzések' if source == 'notes' else 'Faliújság',
                'type_label': str(type_label or ''),
            })
        return sorted(notices, key=lambda item: item['date'], reverse=True)


    def get_notes(self, date=None):
        return self._get('notes', {'datumTol': date} if date else None)

    def get_events(self):
        return self._get('events')

    def get_class_averages(self):
        return self._get('classAverages')

    def get_messages(self, message_type):
        if message_type not in ('beerkezett', 'elkuldott', 'torolt'):
            raise ValueError('Érvénytelen üzenetmappa.')
        # Match the current official web client. The alternate /sajat endpoint
        # can return an empty list even when these folders contain messages.
        payload = self._get('messages', base_url=self.admin_url, suffix='/' + message_type)
        return self.convert_messages(payload)

    def get_message_details(self, message_id, max_retries=3):
        endpoint = self.endpoints['message_details'].format(id=int(message_id))
        return self.convert_message_details(self._get(endpoint, base_url=self.admin_url))

    def get_guardian_data(self):
        return self._get('guardian')

    def get_device_state(self):
        return bool(self._get('device_state'))

    def get_registration_state(self):
        return bool(self._get('registration_state'))

    def get_consulting_hours(self, start_date=None, end_date=None):
        params = {k: v for k, v in {'datumTol': start_date, 'datumIg': end_date}.items() if v}
        return self.convert_consulting_hours(self._get('consulting_hours', params))

    def convert_consulting_hours(self, json_data):
        # The API groups each teacher's meetings under Fogadoorak. Flatten them
        # for the cards while keeping the teacher and the user's reserved slot.
        if not isinstance(json_data, list):
            raise KretaAPIError('A KRÉTA érvénytelen fogadóóra-listát küldött.', api_code='API_FORMAT')
        hours = []
        for group in json_data:
            if not isinstance(group, dict) or not isinstance(group.get('Fogadoorak'), list):
                raise KretaAPIError('A KRÉTA érvénytelen fogadóóra-listát küldött.', api_code='API_FORMAT')
            teacher = self._label(group.get('Tanar'))
            for hour in group['Fogadoorak']:
                if not isinstance(hour, dict):
                    raise KretaAPIError('A KRÉTA érvénytelen fogadóóra-listát küldött.', api_code='API_FORMAT')
                start = self._datetime(hour.get('KezdoIdopont'))
                end = self._datetime(hour.get('VegIdopont'))
                reserved = []
                for slot in hour.get('Idopontok') or []:
                    if isinstance(slot, dict) and slot.get('IsJelentkeztem') is True:
                        slot_start = self._datetime(slot.get('KezdoIdopont'))
                        if slot_start:
                            reserved.append(slot_start.strftime('%Y-%m-%d %H:%M'))
                hours.append({
                    'uid': hour.get('Uid', ''), 'tanarNev': teacher,
                    'idopont': start.strftime('%Y-%m-%d %H:%M') if start else '',
                    'vegIdopont': end.strftime('%Y-%m-%d %H:%M') if end else '',
                    'teremNev': self._label(hour.get('Terem')),
                    'foglalas': {'idopont': ', '.join(reserved)} if reserved else None,
                })
        return sorted(hours, key=lambda hour: hour['idopont'])

    def get_consulting_hour(self, uid):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', uid):
            raise ValueError('Érvénytelen fogadóóra.')
        return self._get('consulting_hours', suffix='/' + uid)

    def get_lep_events(self):
        return self._get('lep_events')

    def update_lep_event_permission(self, event_id, is_permitted):
        return self._change('POST', 'lep_events', {'eventId': event_id, 'isPermitted': str(is_permitted).lower()}, '/GondviseloEngedelyezes')

    def get_class_masters(self, uids=None):
        return self._get('class_masters', {'Uids': uids} if uids else None)

    def get_timetable_weeks(self):
        return self._get('timetable_weeks')

    def update_contact_info(self, email=None, phone=None):
        return self._make_request('POST', self.endpoints['contact'],
                                 data={k: v for k, v in {'email': email, 'telefonszam': phone}.items() if v}).text

    def update_bank_account(self, account_number, owner_name, owner_type_id, bank_name):
        if isinstance(owner_type_id, bool) or not str(owner_type_id).isdigit():
            raise ValueError('Válaszd ki a számlatulajdonos típusát.')
        self._check_bank_access()
        return self._change('POST', 'bank_account', {
            'BankszamlaSzam': account_number,
            'BankszamlaTulajdonosNeve': owner_name,
            'BankszamlaTulajdonosTipusId': int(owner_type_id),
            'SzamlavezetoBank': bank_name,
        })


    def delete_bank_account(self):
        self._check_bank_access()
        return self._change('DELETE', 'bank_account')

    def _check_bank_access(self):
        if self.get_student_data().get('bank_read_only') is True:
            raise KretaAPIError('A KRÉTA nem engedélyezte a bankszámla módosítását.', api_code='API_HTTP', status=403)

    def submit_covid_form(self):
        return self._change('POST', 'covid_form')

    @staticmethod
    def _datetime(value):
        return local_datetime(value)

    @staticmethod
    def _label(value, key='Nev', default=''):
        return (value or {}).get(key, default) if isinstance(value, dict) else default

    def convert_school_year_dates(self, json_data):
        dates = []
        for item in json_data or []:
            description = self._label(item.get('Naptipus'), 'Leiras')
            parsed = self._datetime(item.get('Datum'))
            if parsed and any(name in description for name in ('Utolsó tanítási nap', 'Első tanítási nap', 'Első félév vége')):
                dates.append({'date': parsed.strftime('%Y-%m-%d'), 'description': description})
        return dates

    def convert_student_data(self, data):
        birth = self._datetime(data.get('SzuletesiDatum'))
        bank = data.get('Bankszamla')
        return {'student_name': data.get('Nev'), 'birth_name': data.get('SzuletesiNev'),
                'birth_place': data.get('SzuletesiHely'), 'mother_name': data.get('AnyjaNeve'),
                'phone_number': data.get('Telefonszam'), 'email': data.get('EmailCim'),
                'addresses': data.get('Cimek'), 'birth_date': birth.strftime('%Y-%m-%d') if birth else None,
                'school_name': self._label(data.get('Intezmeny'), 'TeljesNev'),
                'bank_read_only': bank.get('IsReadOnly') if isinstance(bank, dict) else None}

    def convert_lessons(self, json_data):
        lessons = []
        for item in json_data or []:
            lesson_type = self._label(item.get('Tipus'))
            if lesson_type not in ('TanitasiOra', 'OrarendiOra', 'ElmaradtOra', 'UresOra'):
                continue
            start = self._datetime(item.get('KezdetIdopont'))
            end = self._datetime(item.get('VegIdopont'))
            if not start or not end:
                continue
            try:
                period = int(item.get('Oraszam') or 0)
            except (TypeError, ValueError):
                period = 0
            lessons.append({
                'id': item.get('Uid', ''), 'name': item.get('Nev', ''),
                'subject': self._label(item.get('Tantargy')), 'start_time': start.strftime('%H:%M'),
                'end_time': end.strftime('%H:%M'), 'date': start.strftime('%Y-%m-%d'), 'index': period,
                'classroom': item.get('TeremNeve', ''), 'teacher': item.get('TanarNeve', ''),
                'substitute_teacher': item.get('HelyettesTanarNeve', ''), 'topic': item.get('Tema', ''),
                'state': self._label(item.get('Allapot'), 'Leiras'), 'type': lesson_type,
                'group': self._label(item.get('OsztalyCsoport')), 'yearly_ordinal': item.get('TanitasiOraEvesSorszama', ''),
                'description': item.get('Megjegyzes', ''),
            })
        return sorted(lessons, key=lambda row: (row['date'], row['start_time'], row['index']))

    def convert_grades(self, json_data):
        grades = []
        for item in json_data or []:
            if not isinstance(item, dict):
                continue
            text = item.get('SzovegesErtek') or ''
            raw_value = item.get('SzamErtek')
            if raw_value is None:
                raw_value = text
            try:
                number = float(raw_value)
                numeric_value = int(number) if number.is_integer() and 1 <= number <= 5 else 0
            except (ValueError, TypeError, OverflowError):
                numeric_value = 0
            if not text and not numeric_value:
                continue
            try:
                weight = float(item.get('SulySzazalekErteke') if item.get('SulySzazalekErteke') is not None else 100)
                if not 0 <= weight <= 10000:
                    weight = 100
            except (ValueError, TypeError):
                weight = 100
            date = self._datetime(item.get('KeszitesDatuma') or item.get('RogzitesDatuma'))
            grades.append({
                'id': item.get('Uid', ''), 'date': date.strftime('%Y-%m-%d') if date else '',
                'topic': item.get('Tema') or '', 'type': self._label(item.get('Tipus')),
                'type_name': self._label(item.get('Tipus'), 'Leiras'), 'value': str(text or numeric_value),
                'numeric_value': numeric_value, 'teacher': item.get('ErtekeloTanarNeve') or '',
                'subject': self._label(item.get('Tantargy'), default='Ismeretlen tantárgy') or 'Ismeretlen tantárgy',
                'weight': weight,
            })
        return sorted(grades, key=lambda grade: grade['date'], reverse=True)

    def convert_homework(self, json_data):
        homework = []
        for item in json_data or []:
            deadline = self._datetime(item.get('HataridoDatuma'))
            added = self._datetime(item.get('RogzitesIdopontja'))
            homework.append({'id': item.get('Uid', ''), 'lesson_name': item.get('TantargyNeve', ''),
                             'home_work_description': item.get('Szoveg', ''),
                             'deadline': deadline.strftime('%Y-%m-%d') if deadline else '',
                             'date_added': added.strftime('%Y-%m-%d') if added else ''})
        return homework

    def convert_messages(self, json_data, message_type=None):
        if not isinstance(json_data, list):
            raise KretaAPIError('A KRÉTA üzenetlistája érvénytelen.', api_code='API_FORMAT')
        messages = []
        for item in json_data:
            if not isinstance(item, dict):
                raise KretaAPIError('A KRÉTA üzenetlistája érvénytelen.', api_code='API_FORMAT')
            nested = isinstance(item.get('uzenet'), dict)
            content = item['uzenet'] if nested else item
            folder_code = self._label(item.get('tipus'), 'kod').lower()
            is_deleted = bool(item.get('isToroltElem')) or folder_code == 'torolt'
            if nested and message_type:
                if not is_deleted and folder_code not in ('beerkezett', 'elkuldott'):
                    raise KretaAPIError('A KRÉTA üzenetmappája érvénytelen.', api_code='API_FORMAT')
                if message_type == 'torolt':
                    if not is_deleted:
                        continue
                elif is_deleted or folder_code != message_type:
                    continue
            try:
                uid = int(item['azonosito'])
            except (KeyError, TypeError, ValueError):
                raise KretaAPIError('A KRÉTA üzenetlistája érvénytelen.', api_code='API_FORMAT') from None
            date = self._datetime(content.get('kuldesDatum' if nested else 'uzenetKuldesDatum'))
            attachments = content.get('csatolmanyok') or []
            messages.append({
                'id': uid,
                'message_id': content.get('azonosito' if nested else 'uzenetAzonosito'),
                'date': date.strftime('%Y-%m-%d %H:%M') if date else '',
                'sender_name': content.get('feladoNev' if nested else 'uzenetFeladoNev') or '',
                'sender_title': content.get('feladoTitulus' if nested else 'uzenetFeladoTitulus') or '',
                'subject': content.get('targy' if nested else 'uzenetTargy') or 'Nincs tárgy',
                'has_attachment': bool(attachments) if nested else bool(item.get('hasCsatolmany')),
                'is_read': bool(item.get('isElolvasva')),
            })
        return sorted(messages, key=lambda item: (item['date'], item['id']), reverse=True)


    def convert_message_details(self, json_data):
        if not isinstance(json_data, dict) or not isinstance(json_data.get('uzenet'), dict):
            raise KretaAPIError('A KRÉTA üzenete érvénytelen.', api_code='API_FORMAT')
        content = json_data['uzenet']
        try:
            uid = int(json_data['azonosito'])
        except (KeyError, TypeError, ValueError):
            raise KretaAPIError('A KRÉTA üzenete érvénytelen.', api_code='API_FORMAT') from None
        date = self._datetime(content.get('kuldesDatum'))
        def descriptor(value):
            value = value if isinstance(value, dict) else {}
            return {'id': value.get('azonosito'), 'code': value.get('kod') or '',
                    'name': value.get('nev') or '', 'description': value.get('leiras') or ''}
        recipients = content.get('cimzettLista') or []
        attachments = content.get('csatolmanyok') or []
        return {
            'id': uid, 'is_read': bool(json_data.get('isElolvasva')),
            'is_deleted': bool(json_data.get('isToroltElem')),
            'type': descriptor(json_data.get('tipus')),
            'message': {
                'id': content.get('azonosito'),
                'date': date.strftime('%Y-%m-%d %H:%M') if date else '',
                'sender_name': content.get('feladoNev') or '',
                'sender_title': content.get('feladoTitulus') or '',
                'text': content.get('szoveg') or '',
                'subject': content.get('targy') or 'Nincs tárgy',
                'status': descriptor(content.get('statusz')),
                'recipients': [
                    {'id': recipient.get('azonosito'), 'kreta_id': recipient.get('kretaAzonosito'),
                     'name': recipient.get('nev') or '', 'type': descriptor(recipient.get('tipus'))}
                    for recipient in recipients if isinstance(recipient, dict)],
                'attachments': [
                    {'id': attachment.get('azonosito'), 'filename': attachment.get('fajlNev') or ''}
                    for attachment in attachments if isinstance(attachment, dict)],
            },
        }
