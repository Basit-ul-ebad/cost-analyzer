# Cost Analyzer

**Live demo:** https://cost-analyzer-6lae.onrender.com (free tier: first load after idle takes ~1 minute; data resets when the server restarts)

A small Flask web app for comparing **estimated vs actual project costs**, built for the Software Quality Engineering assignment (Basit Ul Ebad, FA23-BSE-026).

## Features

| Requirement | What the app does |
| --- | --- |
| 1. Data entry | Create projects, add cost items by hand, or upload an Excel (`.xlsx`) / CSV file |
| 2. Calculations | Variance (Actual − Estimated), % variance, on/over/under-budget status, category totals |
| 3. Charts | Estimated vs actual by category, variance per item, share of actual spend (Chart.js) |
| 4. AI feature | Unusual-expense detection (IQR outliers + overrun rules) and an AI cost summary via Groq (Llama 3.3 70B), with a rule-based fallback |
| 5. Report | Downloadable PDF report (charts, tables, AI summary) and Excel export |
| Security | Accounts with private projects, CSRF protection, rate limiting, strict security headers, hardened uploads, security event log |

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env      # macOS/Linux: cp .env.example .env
# edit .env and paste your free Groq API key from https://console.groq.com
python app.py
```

Open http://127.0.0.1:5000, create an account, then create a project. A sample file is available from the project page ("Download sample template") or at `static/sample_costs.csv`.

## Project layout

```
app.py            Flask routes, upload parsing and validation, database
calculations.py   Variance, % variance, status, category totals
ai.py             Unusual-expense detection + Groq AI summary (with fallback)
report.py         PDF report (reportlab + matplotlib) and Excel export (openpyxl)
tests/            pytest security and functional tests
docs/             Security checklist and test results
templates/        Jinja2 HTML pages
static/           CSS, chart script, bundled Chart.js, sample CSV
screenshots/      Screenshots for the submission
sample_output/    Example PDF and Excel reports
```

## Formulas

- **Variance** = Actual − Estimated (positive = overspent)
- **% Variance** = Variance ÷ Estimated × 100 (shown as n/a when the estimate is 0)
- **Status**: over budget above +2%, under budget below −2%, otherwise on budget

## Tests

```bash
pip install pytest pypdf
python -m pytest -v
```

41 tests: 35 security tests (one class per threat T1–T8, plus SQL injection) and 6 functional tests. Latest output: [`docs/test-results.txt`](docs/test-results.txt).

## Security

- Threat-and-mitigation analysis: see the threat analysis document (8 threats, attack scenarios, top 3).
- Pre-deployment checklist: [`docs/security-checklist.md`](docs/security-checklist.md)
- Security events (failed logins, denied access, CSRF, rate limits, rejected uploads) are logged to `instance/security.log`.
- Never commit `.env`; it holds your secret key and API key and is already in `.gitignore`.

## Deploy

`render.yaml` deploys to Render's free tier: **New → Blueprint →** pick this repo, then paste `GROQ_API_KEY` when asked. `SECRET_KEY` is generated automatically and `APP_ENV=production` turns on HTTPS-only cookies and HSTS.
