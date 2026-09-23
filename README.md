# KRETÉN

A Hungarian, responsive Flask web client for KRÉTA. The existing IDP login and OAuth authorization-code exchange are retained; the interface follows the user's KRETÉN concept and adapts Firka's student-focused navigation and screen structure for the web.

## Run locally

Python 3.10+ on Linux is required (the session store uses POSIX file locks).

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5050. Search for your institution and select it, then enter your own KRÉTA credentials in the browser. The selected institution **code**, not its numeric identifier, is submitted. For example, Biatorbágyi Innovatív Technikum és Gimnázium uses `bit-edu`; its identifier is `910018`. Manual institution-code entry remains available.

Stop this local server from any terminal in the project directory with `.venv/bin/python app.py --stop`. If you started it with a custom port, use the same port, for example `PORT=5053 .venv/bin/python app.py --stop`. The stop command verifies that the port has closed; older servers started before this control was added must be stopped through their original process.

The full public institution directory is cached when available. If that service is unavailable, the picker searches KRÉTA's official institution selector as you type. It clearly marks partial/offline results.

Do not place credentials in source files, tests, screenshots, environment files, or logs. The application never persists a password. Do not use old token files for login.

## Pages and behavior

- Overview: next lesson, today's timetable, recent grades, weighted average, upcoming homework/tests, school-year dates. An unavailable section does not prevent the others from rendering.
- Timetable: previous/current/next week, subject/teacher/room filtering, canceled and substituted lessons, expandable details. Weekend lessons are retained when present. Times use Europe/Budapest.
- Grades: subject groups, explicit weights, textual grades, semester filters, reveal-on-click preference, overall/monthly/per-subject statistics and custom date-range analysis.
- Homework and announced tests; homework ranges longer than three weeks are split into bounded API requests and merged without duplicate IDs. Absences show justification status and lateness minutes.
- Inbox/sent/deleted folders and message details use the same separate folder endpoints as KRÉTA’s current web client; parsing tolerates optional message fields and nested detail metadata. These requests use separate service headers because the diary mobile identifier causes connection resets at the e-administration service. External HTML is rendered as non-executable text.
- Consultation schedules, LEP events and permissions, profile/contact/bank-account actions, appearance settings.
- Desktop sidebar, mobile drawer/bottom navigation, light/dark mode, locally served CSS/JS and concept logos. No CDN dependency.
- Animated login background with two custom colors, presets and a pause control. Appearance preferences are saved in browser local storage; reduced-motion preferences are respected.

Numeric grade statistics accept only 1–5, apply the supplied percentage weights, and exclude textual/non-numeric entries from arithmetic. Empty data displays a dash rather than a fabricated zero. Charts show monthly weighted averages. All calculations run in Python; templates no longer call the unavailable `max()` global that caused the marks page failure.

The old consultation reservation route returned success without doing anything. It now returns HTTP 501 honestly, and the UI directs users to official KRÉTA booking. Message attachment download is not implemented; filenames and the limitation are shown. Bank-account values are not displayed here. The profile respects the API’s read-only flag and blocks write/delete calls when it is set. Otherwise updates use the Hungarian API field names and a numeric owner type; KRÉTA may still require additional authentication on its official profile. Consultation access denied by KRÉTA is shown explicitly, rather than as an empty appointment list. No mobile notifications, offline synchronization, or complete Firka feature parity is claimed.

## Authentication and concurrent hosting

- Each successful login creates a random session handle. The signed browser cookie contains only this handle and Flask's CSRF/session metadata; account names and OAuth tokens stay on the server.
- Each request gets its own API client. There are no shared global user/token objects and no reads/writes to the legacy `refresh_token.txt`.
- `instance/sessions.sqlite3` holds per-session tokens with restrictive file permissions and a 12-hour lifetime. Refresh is serialized per session across workers, retries one rejected API request, and cannot resurrect a logged-out session.
- Logout revokes the local server session. It does not sign the user out of unrelated KRÉTA applications.
- `instance/secret.key` is created once and shared across restarts/workers. `SECRET_KEY` may instead come from a deployment secret manager. Do not commit either the instance directory or deployment secrets.
- CSRF protection covers mutations; cookies are HttpOnly and SameSite=Lax. Private pages are marked no-store. A content security policy blocks third-party scripts and framing. Request/response bodies and credentials are not logged. Failure diagnostics contain only allowlisted operation/category codes and HTTP status; the same support codes appear in errors shown to the user.

For a single Linux host with several workers, use the same local instance directory for all workers. A reverse proxy should provide HTTPS:

```sh
KRETEN_HTTPS=1 .venv/bin/gunicorn --workers 2 --threads 4 --timeout 180 --bind 127.0.0.1:5050 app:app
```

Set `KRETEN_HTTPS=1` only with HTTPS; it enables Secure cookies and HSTS. Configure proxy access logs to omit sensitive request bodies/headers. Use `KRETEN_INSTANCE_PATH` for an absolute private instance directory if needed. SQLite/POSIX locking is intended for one host's local disk. Multi-host hosting needs a shared external session store and distributed refresh locking. The Flask development server binds loopback and does not run a debugger.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Additional API contract tests cover bounded homework requests, grouped consultation responses, nested mailbox folders and optional fields, service-specific headers, bank write permissions/payloads, and private failure diagnostics.

The suite uses only fictional fixtures and disposable session stores, and blocks real network access in integration tests. It covers all main pages populated/empty, the marks regression, weighted statistics/date boundaries, institution code mapping and search fallbacks, CSRF and HTML injection, cookie privacy, independent users/institutions, logout, OAuth PKCE/state, token refresh races, and separate worker processes.

Desktop and mobile browser testing uses a separate loopback-only synthetic preview harness outside the production project. Real authenticated API verification requires the user to enter credentials themselves in the local browser. Never treat synthetic data as a live API success.

## Design references and assets

- KRETÉN concept: https://e-kreten.github.io/naplo/login/
- Firka: https://github.com/QwIT-Development/firka
- Logo origins and reuse notes: `static/brand/ASSETS.md`.

Firka's repository is licensed CC BY-NC-SA 4.0. This client uses an independent web implementation inspired by its public screen structure; no Firka source or assets were copied. The provided KRETÉN concept logos were retrieved from the user's referenced concept site.
