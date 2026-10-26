# Pre-deployment security checklist

Go through every item before putting Cost Analyzer online. **Status** is the state of this prototype as of 26 Oct 2026: ✅ done and tested, ⚙️ done when deploying (a hosting setting), ⏳ future work.

## 1. Configuration and secrets

| # | Check | How to verify | Status |
|---|---|---|---|
| 1.1 | `APP_ENV=production` is set on the host | Host dashboard → Environment | ⚙️ |
| 1.2 | `SECRET_KEY` is a random value of 32+ characters, unique to production | App refuses to start otherwise (`test_production_refuses_to_start_without_strong_secret_key`) | ✅ |
| 1.3 | Debug mode is off; the app runs under gunicorn, not `python app.py` | `test_debug_mode_is_off`; start command is `gunicorn app:app` | ✅ |
| 1.4 | `GROQ_API_KEY` is stored only in the host's secret settings | `git log -p \| grep gsk_` returns nothing; `.env` is in `.gitignore` | ✅ |
| 1.5 | No secrets, databases or logs are committed to Git | `git ls-files` shows no `.env`, `instance/`, `*.db`, `*.log` | ✅ |
| 1.6 | Any key that was ever exposed (chat, screenshot, commit) has been rotated | Regenerate in the Groq console | ⚙️ |

## 2. Authentication and access control

| # | Check | How to verify | Status |
|---|---|---|---|
| 2.1 | Every page except sign-in, sign-up, health check and the sample CSV requires a login | `test_anonymous_user_is_sent_to_login` | ✅ |
| 2.2 | Every project route checks ownership and returns 404 for other users | `TestT1UnauthorizedProjectAccess` (6 tests) | ✅ |
| 2.3 | Passwords are hashed (scrypt) and never stored or logged in plain text | `test_passwords_are_hashed` | ✅ |
| 2.4 | Password rules: 8–128 characters, not equal to the username | Registration form and server check | ✅ |
| 2.5 | Session ID is regenerated at login (prevents session fixation) | `session.clear()` in `login()` / `register()` | ✅ |
| 2.6 | Sessions expire after 8 hours | `PERMANENT_SESSION_LIFETIME` | ✅ |
| 2.7 | Login errors don't reveal whether a username exists | Same message plus a dummy hash check for unknown users | ✅ |
| 2.8 | Multi-factor authentication, password reset by email, account lockout | | ⏳ |

## 3. Input, output and files

| # | Check | How to verify | Status |
|---|---|---|---|
| 3.1 | Uploads: only `.csv` / `.xlsx`, ≤ 2 MB, ≤ 1,000 rows, file signature matches extension, zip-bomb guard | `TestT4InvalidFileUploads` (9 tests) | ✅ |
| 3.2 | Every field validated on the server (lengths, numbers 0–1 trillion, no NaN) | `test_bad_values` | ✅ |
| 3.3 | All database queries use parameters (no SQL built from strings) | Code review; `test_login_with_sql_injection_fails_safely` | ✅ |
| 3.4 | All output is HTML-escaped; chart data goes through `tojson` | `TestT3CrossSiteScripting` | ✅ |
| 3.5 | Excel export neutralises formula cells (`=`, `+`, `-`, `@`) | `test_formula_cells_are_neutralised_in_excel_export` | ✅ |
| 3.6 | PDF report escapes user text (no markup injection) | Manual test with `<b>`, `<font>` names | ✅ |
| 3.7 | AI receives only aggregated data, its output is escaped and labelled as AI | `TestT8PromptInjection` | ✅ |
| 3.8 | Redirects after login only go to local paths | `test_login_redirect_cannot_point_to_another_site` | ✅ |
| 3.9 | Antivirus scan of uploaded files | | ⏳ |

## 4. Browser protections

| # | Check | How to verify | Status |
|---|---|---|---|
| 4.1 | CSRF token on every form; logout is POST only | `TestT6CrossSiteRequestForgery` | ✅ |
| 4.2 | `Content-Security-Policy` with no inline scripts; Chart.js is bundled, no CDN | `test_security_headers_present`; no CSP errors in the browser console | ✅ |
| 4.3 | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` | `test_security_headers_present` | ✅ |
| 4.4 | Cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` with the `__Host-` prefix in production | `test_session_cookie_flags` | ✅ |
| 4.5 | Pages and reports sent with `Cache-Control: no-store` | `test_security_headers_present` | ✅ |

## 5. Transport and hosting

| # | Check | How to verify | Status |
|---|---|---|---|
| 5.1 | Site is served only over HTTPS; HTTP redirects to HTTPS | Host provides TLS; HSTS header is sent in production | ⚙️ |
| 5.2 | App trusts exactly one proxy hop (correct client IP for rate limits) | `ProxyFix(x_for=1)` in production | ✅ |
| 5.3 | Database is on persistent storage and backed up | Free hosting tiers reset the disk; a managed database is needed for real use | ⏳ |
| 5.4 | Dependencies are pinned and checked for known vulnerabilities | Run `pip-audit` before each deploy | ⏳ |

## 6. Availability and abuse

| # | Check | How to verify | Status |
|---|---|---|---|
| 6.1 | Rate limits: login 10 / 15 min per IP, sign-up 5 / hour per IP, AI summary 5 / min and 30 / day, reports 10 / min, uploads 10 / min, 300 / min overall | `TestT5ExcessiveRequests` | ✅ |
| 6.2 | AI call has a timeout, results are cached, rule-based fallback works | `ai.py`; `test_rule_based_summary_without_api_key` | ✅ |
| 6.3 | Shared rate-limit store (Redis) when running more than one server process | `RATELIMIT_STORAGE_URI` | ⏳ |

## 7. Monitoring and testing

| # | Check | How to verify | Status |
|---|---|---|---|
| 7.1 | Security events logged: failed logins, denied project access, CSRF failures, rate limiting, rejected uploads | `instance/security.log`; `test_access_denial_is_logged` | ✅ |
| 7.2 | Error pages never show stack traces | `test_errors_show_no_stack_trace` | ✅ |
| 7.3 | Full test suite passes: `python -m pytest -v` | 41 / 41 passing | ✅ |
| 7.4 | Health-check endpoint for the host | `GET /healthz` | ✅ |
| 7.5 | Alerts on unusual log activity; external penetration test | | ⏳ |
