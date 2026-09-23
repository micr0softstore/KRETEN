"""KRETÉN — responsive, session-isolated KRÉTA web client."""
from datetime import datetime, timedelta
from pathlib import Path
import os
from flask import Flask, request, jsonify, session, render_template, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from dotenv import load_dotenv
from kreta_utils import KretaUtils, AuthenticationError, KretaAPIError
from api_diagnostics import report_failure
from session_store import SessionStore, load_or_create_secret
from institutions import InstitutionDirectory, validate_institution_code
from view_models import today, school_year, grade_summary, absence_rows, test_rows, plain_text

load_dotenv()
app = Flask(__name__)
instance = Path(os.environ.get('KRETEN_INSTANCE_PATH', app.instance_path))
app.secret_key = os.environ.get('SECRET_KEY') or load_or_create_secret(instance / 'secret.key')
app.config.update(SESSION_COOKIE_NAME='kreten_session', SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('KRETEN_HTTPS') == '1',
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=64 * 1024)
store = SessionStore(instance / 'sessions.sqlite3')
directory = InstitutionDirectory()
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'A folytatáshoz jelentkezz be.'
csrf = CSRFProtect(app)
app.jinja_env.filters['plain_text'] = plain_text
app.jinja_env.filters['decimal'] = lambda value: '—' if value is None else f'{value:.2f}'.replace('.', ',')


def refresh_credentials(code, tokens):
    client = KretaUtils.from_tokens('', code, tokens)
    return client.refresh_tokens()


class User(UserMixin):
    def __init__(self, sid, record):
        self.id = sid
        self.username = record['username']
        self.klik_id = record['institute_code']
        def refresh_handler(stale_access_token=None):
            refreshed = store.ensure_fresh(sid, refresh_credentials, force=True,
                                           stale_access_token=stale_access_token)
            if not refreshed:
                raise ValueError('Lejárt munkamenet.')
            return refreshed['token_data']
        self.kreta = KretaUtils.from_tokens(self.username, self.klik_id, record['token_data'],
                                            refresh_handler=refresh_handler)


@login_manager.user_loader
def load_user(sid):
    try:
        record = store.ensure_fresh(sid, refresh_credentials)
        return User(sid, record) if record else None
    except Exception:
        # No credential-bearing exception text is sent to a page or logs.
        store.delete(sid)
        return None


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/') or request.is_json:
        return jsonify(error='A munkamenet lejárt. Jelentkezz be újra.'), 401
    return redirect(url_for('login'))


@app.after_request
def private_responses(response):
    if not request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store, private'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if app.config['SESSION_COOKIE_SECURE']:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.errorhandler(CSRFError)
def csrf_error(error):
    if request.is_json or request.path.startswith('/api/'):
        return jsonify(error='A biztonsági token lejárt. Frissítsd az oldalt.'), 400
    flash('A biztonsági token lejárt. Próbáld újra a frissített oldalon.', 'error')
    return redirect(url_for('login') if not current_user.is_authenticated else url_for('dashboard'))


@app.context_processor
def shared_context():
    return {'today': today(), 'school_year_label': f'{school_year()[0][:4]} / {school_year()[1][:4]}'}


def fetch_part(label, fetch, default):
    try:
        value = fetch()
        return default if value is None else value
    except Exception as error:
        flash(f'{label}: {report_failure(label, error)}', 'error')
        return default


def grades_for_request():
    period = request.args.get('period', 'all')
    start, end = school_year(period)
    return fetch_part('Értékelések', lambda: current_user.kreta.get_grades(start, end), []), period


@app.route('/')
def index():
    return redirect(url_for('dashboard' if current_user.is_authenticated else 'login'))


@app.route('/api/institutions')
def institutions():
    return jsonify(directory.get(request.args.get('q', '')[:200]))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        try:
            code = validate_institution_code(request.form.get('institute_code'))
        except ValueError:
            flash('Válassz intézményt, vagy adj meg érvényes intézménykódot.', 'error')
            return redirect(url_for('login'))
        if not username or not password:
            flash('Add meg a felhasználóneved és a jelszavad.', 'error')
            return redirect(url_for('login'))
        try:
            client = KretaUtils(username, None, code)
            tokens = client.login(username, password, code)
        except AuthenticationError as error:
            flash(error.public_message, 'error')
            return redirect(url_for('login'))
        except Exception:
            flash('Nem sikerült bejelentkezni. A kliens nem tudta feldolgozni a belépést. (Hibakód: LOGIN_CLIENT)', 'error')
            return redirect(url_for('login'))
        sid = None
        try:
            sid = store.create(username, code, tokens)
            session.clear()
            login_user(User(sid, store.get(sid)), remember=False)
            session.permanent = True
            return redirect(url_for('dashboard'))
        except Exception:
            if sid:
                try:
                    store.delete(sid)
                except Exception:
                    pass
            session.clear()
            flash('A KRÉTA-belépés sikerült, de a szerver nem tudta menteni a munkamenetedet. (Hibakód: LOGIN_STORAGE)', 'error')
            return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/dashboard')
@login_required
def dashboard():
    api = current_user.kreta
    start, end = school_year()
    now = datetime.now().astimezone()
    from zoneinfo import ZoneInfo
    now = now.astimezone(ZoneInfo('Europe/Budapest'))
    day = today().isoformat()
    student = fetch_part('Tanulói adatok', api.get_student_data, {})
    lessons = fetch_part('Órarend', lambda: api.get_lessons(day, (today() + timedelta(days=7)).isoformat()), [])
    lessons = sorted(lessons, key=lambda row: (row.get('date', ''), row.get('start_time', '')))
    upcoming = [l for l in lessons if (l.get('date', ''), l.get('end_time', '')) > (day, now.strftime('%H:%M')) and 'elmaradt' not in l.get('state', '').lower()]
    grades = fetch_part('Értékelések', lambda: api.get_grades(start, end), [])
    homework = fetch_part('Házi feladatok', lambda: api.get_homework(day, (today() + timedelta(days=14)).isoformat()), [])
    tests = fetch_part('Dolgozatok', lambda: api.get_announced_tests(day), [])
    dates = fetch_part('Tanév rendje', api.get_school_year_dates, [])
    return render_template('dashboard.html', student_data=student, next_lesson=upcoming[0] if upcoming else None,
                           today_lessons=[l for l in lessons if l.get('date') == day], stats=grade_summary(grades),
                           homework=sorted(homework, key=lambda h: h.get('deadline', ''))[:4], tests=test_rows(tests)[:3],
                           school_year_dates=dates)


@app.route('/orarend')
@login_required
def orarend():
    try:
        selected = datetime.strptime(request.args.get('week', today().isoformat()), '%Y-%m-%d').date()
    except ValueError:
        selected = today()
    monday = selected - timedelta(days=selected.weekday())
    dates = [monday + timedelta(days=n) for n in range(7)]
    lessons = fetch_part('Órarend', lambda: current_user.kreta.get_lessons(dates[0].isoformat(), dates[-1].isoformat()), [])
    schedule = {d.isoformat(): sorted([l for l in lessons if l.get('date') == d.isoformat()], key=lambda l: l.get('start_time', '')) for d in dates}
    visible = dates if any(schedule[d.isoformat()] for d in dates[5:]) else dates[:5]
    return render_template('orarend.html', schedule=schedule, days=visible, start_date=dates[0], end_date=dates[-1],
                           day_names=['Hétfő', 'Kedd', 'Szerda', 'Csütörtök', 'Péntek', 'Szombat', 'Vasárnap'],
                           prev_week=(monday-timedelta(days=7)).isoformat(), next_week=(monday+timedelta(days=7)).isoformat())


@app.route('/jegyek')
@login_required
def jegyek():
    grades, period = grades_for_request()
    return render_template('jegyek.html', stats=grade_summary(grades), period=period)


@app.route('/jegyek/statisztika')
@login_required
def jegyek_statisztika():
    grades, period = grades_for_request()
    subject = request.args.get('subject', '')
    subjects = sorted(set(g.get('subject') or '' for g in grades))
    if subject:
        grades = [g for g in grades if g.get('subject') == subject]
    return render_template('jegyek_statisztika.html', stats=grade_summary(grades), period=period, subjects=subjects, selected_subject=subject)


@app.route('/api/jegyek/statisztika', methods=['POST'])
@login_required
def api_jegyek_statisztika():
    data = request.get_json(silent=True) or {}
    start, end = school_year(data.get('period', 'all'))
    try:
        grades = current_user.kreta.get_grades(start, end)
        if data.get('subject') not in (None, '', 'all'):
            grades = [g for g in grades if g.get('subject') == data['subject']]
        stats = grade_summary(grades)
        overall = {'average': stats['simple_average'], 'weighted_average': stats['average'], 'best': stats['best'],
                   'worst': stats['worst'], 'count': stats['numeric_count'], 'mode': stats['mode']}
        return jsonify(overall_stats=overall, grade_distribution={'labels': ['1','2','3','4','5'], 'values': stats['distribution'],
            'percentages': [n / stats['numeric_count'] * 100 if stats['numeric_count'] else 0 for n in stats['distribution']]},
            trend_data={'labels': [t['label'] for t in stats['trend']], 'values': [t['value'] for t in stats['trend']]},
            subject_averages={'labels': [s['name'] for s in stats['subjects']], 'values': [s['average'] for s in stats['subjects']]})
    except Exception:
        return jsonify(error='Az értékelések nem érhetők el.'), 502


@app.route('/api/jegyek/egyeni-elemzes', methods=['POST'])
@login_required
def api_jegyek_egyeni_elemzes():
    data = request.get_json(silent=True) or {}
    try:
        start = datetime.strptime(data.get('startDate', ''), '%Y-%m-%d').date()
        end = datetime.strptime(data.get('endDate', ''), '%Y-%m-%d').date()
        if start > end or (end-start).days > 730:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error='Adj meg érvényes, legfeljebb két éves dátumtartományt.'), 400
    try:
        grades = sorted(current_user.kreta.get_grades(start.isoformat(), end.isoformat()), key=lambda g: g.get('date', ''))
        from view_models import average
        if data.get('metric') == 'weighted_avg':
            results = {'Súlyozott átlag': average(grades)}
        elif data.get('metric') == 'improvement':
            midpoint = len(grades) // 2
            first, second = average(grades[:midpoint]), average(grades[midpoint:])
            results = {'Fejlődési trend': round(second-first, 2) if first is not None and second is not None else None}
        else:
            return jsonify(error='Ismeretlen mutató.'), 400
        return jsonify(results=results)
    except Exception:
        return jsonify(error='Az elemzés most nem érhető el.'), 502


@app.route('/hazi-feladatok')
@login_required
def homework_page():
    day = today()
    homework = fetch_part('Házi feladatok', lambda: current_user.kreta.get_homework((day-timedelta(days=14)).isoformat(), (day+timedelta(days=30)).isoformat()), [])
    tests = fetch_part('Dolgozatok', lambda: current_user.kreta.get_announced_tests(day.isoformat()), [])
    return render_template('homework.html', homework=sorted(homework, key=lambda h: h.get('deadline', '')), tests=test_rows(tests))


@app.route('/hianyzasok')
@login_required
def absences_page():
    rows = absence_rows(fetch_part('Mulasztások', current_user.kreta.get_absences, []))
    return render_template('absences.html', absences=rows, justified=sum(r['justified'] for r in rows), late_minutes=sum(r['minutes'] for r in rows))


@app.route('/fogadoorak')
@login_required
def fogadoorak():
    access_error = None
    try:
        hours = current_user.kreta.get_consulting_hours()
    except Exception as error:
        hours = []
        message = report_failure('Fogadóórák', error)
        if isinstance(error, KretaAPIError) and error.http_status == 403:
            access_error = message
        else:
            flash(f'Fogadóórák: {message}', 'error')
    return render_template('fogadoorak.html', consulting_hours=hours, access_error=access_error)


@app.route('/fogadoora/foglalas/<uid>', methods=['POST'])
@login_required
def fogadoora_foglalas(uid):
    # The original route claimed success without making a reservation.
    return jsonify(error='Az időpontfoglalás ebben a kliensben még nem érhető el. Foglalj a hivatalos KRÉTA felületén.'), 501


@app.route('/lep')
@login_required
def lep_events():
    return render_template('lep_events.html', events=fetch_part('LEP események', current_user.kreta.get_lep_events, []))


def mutation(call, operation='unknown'):
    try:
        call()
        return jsonify(success=True)
    except Exception as error:
        return jsonify(error=report_failure(operation, error)), 502


@app.route('/lep/engedelyezes/<event_id>', methods=['POST'])
@login_required
def lep_event_permission(event_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('isPermitted'), bool):
        return jsonify(error='Hiányzó döntés.'), 400
    return mutation(lambda: current_user.kreta.update_lep_event_permission(event_id, data['isPermitted']), 'lep_permission')


@app.route('/profil')
@login_required
def profile():
    student = fetch_part('Tanulói adatok', current_user.kreta.get_student_data, {})
    return render_template('profile.html', student_data=student, contact={'email': student.get('email') or '', 'phone': student.get('phone_number') or ''})


@app.route('/profil/elerhetoseg', methods=['POST'])
@login_required
def update_contact():
    data = request.get_json(silent=True) or {}
    return mutation(lambda: current_user.kreta.update_contact_info(email=data.get('email'), phone=data.get('phone')), 'contact_update')


@app.route('/profil/bankszamla', methods=['POST', 'DELETE'])
@login_required
def manage_bank_account():
    if request.method == 'DELETE':
        return mutation(current_user.kreta.delete_bank_account, 'bank_delete')
    data = request.get_json(silent=True) or {}
    if not all(data.get(key) for key in ('accountNumber','ownerName','ownerTypeId','bankName')):
        return jsonify(error='Töltsd ki a bankszámla összes mezőjét.'), 400
    return mutation(lambda: current_user.kreta.update_bank_account(account_number=data['accountNumber'], owner_name=data['ownerName'], owner_type_id=data['ownerTypeId'], bank_name=data['bankName']), 'bank_update')


@app.route('/uzenetek')
@app.route('/uzenetek/<folder>')
@login_required
def uzenetek(folder='iskolai'):
    folders = {'iskolai':'Iskolai értesítések', 'beerkezett':'Beérkezett', 'elkuldott':'Elküldött', 'torolt':'Törölt'}
    folder = folder if folder in folders else 'iskolai'
    notices, messages = [], []
    if folder == 'iskolai':
        start, _ = school_year()
        for source, label in (('notes', 'Feljegyzések'), ('board', 'Faliújság')):
            notices.extend(fetch_part(label, lambda source=source: current_user.kreta.get_school_notices(source, start), []))
        notices.sort(key=lambda notice: notice.get('date', ''), reverse=True)
    else:
        messages = fetch_part('Üzenetek', lambda: current_user.kreta.get_messages(folder), [])
    return render_template('uzenetek.html', messages=messages, notices=notices, current_folder=folder, folders=folders)


@app.route('/uzenetek/reszletek/<int:message_id>')
@login_required
def uzenet_reszletek(message_id):
    return render_template('uzenet_reszletek.html', message=fetch_part('Üzenet', lambda: current_user.kreta.get_message_details(message_id), None))


@app.route('/beallitasok')
@login_required
def settings():
    return render_template('settings.html')


@app.route('/api/refresh', methods=['POST'])
@login_required
def refresh_token():
    try:
        record = store.ensure_fresh(current_user.id, refresh_credentials, force=True)
        if not record:
            raise ValueError()
        return jsonify(success=True)
    except Exception:
        return jsonify(error='A munkamenet nem frissíthető. Jelentkezz be újra.'), 401


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    store.delete(current_user.id)
    logout_user()
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    import argparse
    from local_server import run_local, stop_local
    parser = argparse.ArgumentParser(description='Run or stop the local KRETÉN server.')
    parser.add_argument('--stop', action='store_true', help='Stop the local server on PORT (default: 5050).')
    arguments = parser.parse_args()
    try:
        port = int(os.environ.get('PORT', '5050'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        parser.error('PORT must be a number between 1 and 65535.')
    try:
        if arguments.stop:
            print(stop_local(instance, port))
        else:
            run_local(app, instance, port)
    except (RuntimeError, OSError) as error:
        parser.exit(1, f'{error}\n')
