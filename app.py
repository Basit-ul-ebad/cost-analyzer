"""Cost Analyzer - Flask app: data entry, calculations, charts, AI insights, reports.

Security controls (see docs/security-checklist.md):
  * accounts with hashed passwords; every project route checks ownership (404 otherwise)
  * CSRF tokens on every form (Flask-WTF)
  * rate limits per user / IP (Flask-Limiter)
  * security headers incl. a strict Content-Security-Policy
  * hardened session cookies; debug mode off unless explicitly enabled in development
  * upload validation: extension, size, file signature, zip-bomb guard, row/column/value checks
  * security event log in instance/security.log
"""
import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import zipfile
from datetime import timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import pert
from ai import detect_anomalies, generate_summary
from calculations import analyse_project
from report import build_pdf, build_xlsx, safe_filename

BASE_DIR = Path(__file__).resolve().parent

# Load settings (SECRET_KEY, GROQ_API_KEY, ...) from a local .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PROD = APP_ENV == "production"
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = Path(os.environ.get("DATABASE_PATH", INSTANCE_DIR / "costs.db"))
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UNZIPPED_BYTES = 30 * 1024 * 1024   # an .xlsx is a zip: refuse if it expands beyond this
MAX_ZIP_RATIO = 100                     # ...or if any member compresses more than 100:1
MAX_ROWS = 1000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
WEAK_KEYS = {"", "dev-only-change-me", "change-me-to-a-long-random-string"}


def load_secret_key():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key in WEAK_KEYS or len(key) < 32:
        if IS_PROD:
            raise RuntimeError(
                "SECRET_KEY must be set to a random value of at least 32 characters in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        # Development: a random key per run (logins reset when the server restarts).
        return secrets.token_hex(32)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_NAME="__Host-session" if IS_PROD else "session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PROD,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    WTF_CSRF_TIME_LIMIT=None,  # token lives as long as the session
    RATELIMIT_STORAGE_URI=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    RATELIMIT_HEADERS_ENABLED=True,
)
if IS_PROD:
    # Hosting platforms put the app behind one proxy; trust its X-Forwarded-* headers
    # so rate limits see the real client IP and URLs use https.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

csrf = CSRFProtect(app)

# ---------- security event log ----------
LOG_PATH = DB_PATH.parent / "security.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
security_log = logging.getLogger("security")
security_log.setLevel(logging.INFO)
if not security_log.handlers:
    _fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    security_log.addHandler(_fh)
    security_log.addHandler(logging.StreamHandler())


def log_event(event, **details):
    who = session.get("username", "-")
    extra = " ".join(f"{k}={v!r}" for k, v in details.items())
    security_log.warning(f"{event} user={who} ip={get_remote_address()} path={request.path!r} {extra}")


# ---------- rate limiting ----------
def rate_key():
    """Signed-in users are limited per account, everyone else per IP address."""
    uid = session.get("user_id")
    return f"user:{uid}" if uid else f"ip:{get_remote_address()}"


limiter = Limiter(key_func=rate_key, app=app, default_limits=["300 per minute"])

# Header names we accept from uploaded files, mapped to our internal columns.
COLUMN_ALIASES = {
    "item": {"item", "description", "item name", "expense", "name"},
    "category": {"category", "type", "group"},
    "estimated": {"estimated", "estimated cost", "estimate", "budget", "planned"},
    "actual": {"actual", "actual cost", "spent", "actual spend"},
    # Optional PERT columns (three-point estimates and dependencies)
    "code": {"id", "wbs", "wbs id", "activity id", "task id", "code", "ref"},
    "cost_o": {"optimistic cost", "cost optimistic", "o cost", "cost o", "best case cost", "min cost"},
    "cost_m": {"most likely cost", "likely cost", "m cost", "cost m", "cost most likely"},
    "cost_p": {"pessimistic cost", "cost pessimistic", "p cost", "cost p", "worst case cost", "max cost"},
    "dur_o": {"optimistic days", "optimistic duration", "optimistic time", "o days", "days o", "duration o", "to"},
    "dur_m": {"most likely days", "likely days", "most likely duration", "most likely time", "m days", "days m",
              "duration m", "tm"},
    "dur_p": {"pessimistic days", "pessimistic duration", "pessimistic time", "p days", "days p", "duration p", "tp"},
    "predecessors": {"predecessors", "predecessor", "depends on", "dependencies", "dependency"},
}
PERT_FIELDS = ("code", "cost_o", "cost_m", "cost_p", "dur_o", "dur_m", "dur_p", "predecessors")
ITEM_COLUMNS = ("item", "category", "estimated", "actual") + PERT_FIELDS
CODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,20}$")


# ---------- database ----------
def get_db():
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cost_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            item TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'General',
            estimated REAL NOT NULL,
            actual REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_summaries (
            project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            source TEXT NOT NULL,
            data_hash TEXT NOT NULL,
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Databases created before accounts existed: add the owner column.
    # Old projects keep owner_id NULL and are therefore visible to nobody.
    cols = {r["name"] for r in db.execute("PRAGMA table_info(projects)")}
    if "owner_id" not in cols:
        db.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER REFERENCES users(id)")
    if "target_days" not in cols:
        db.execute("ALTER TABLE projects ADD COLUMN target_days REAL")
    item_cols = {r["name"] for r in db.execute("PRAGMA table_info(cost_items)")}
    for col in PERT_FIELDS:
        if col not in item_cols:
            kind = "TEXT" if col in ("code", "predecessors") else "REAL"
            db.execute(f"ALTER TABLE cost_items ADD COLUMN {col} {kind}")  # names from a fixed tuple
    db.execute("CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id)")
    db.commit()


with app.app_context():
    init_db()


# ---------- authentication & authorisation ----------
def current_user_id():
    return session.get("user_id")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user_id() is None:
            flash("Please sign in to continue", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_project_or_404(project_id):
    """The single access-control check: a project is only visible to its owner.

    Returns 404 (not 403) so other users cannot tell which project ids exist.
    """
    project = get_db().execute(
        "SELECT * FROM projects WHERE id = ? AND owner_id = ?", (project_id, current_user_id())
    ).fetchone()
    if project is None:
        log_event("PROJECT_ACCESS_DENIED", project_id=project_id)
        abort(404)
    return project


def safe_next(target):
    """Only allow redirects to a local path (prevents open redirects after login)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("index")


# Precomputed hash so a login for an unknown username takes as long as a real one.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


# ---------- validation helpers ----------
CURRENCY_RE = re.compile(r"(?i)(usd|pkr|inr|eur|gbp|rs\.?|[$€£₹])")


def parse_amount(raw, field):
    """Turn user input like '1,200.50', '$1,200' or 'Rs 5,000' into a non-negative float."""
    try:
        text = CURRENCY_RE.sub("", str(raw)).replace(",", "").replace(" ", "").strip()
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")
    if value != value or value < 0 or value > 1e12:  # value != value catches NaN
        raise ValueError(f"{field} must be between 0 and 1,000,000,000,000")
    return round(value, 2)


def clean_text(raw, field, max_len=100, default=None):
    text = " ".join(str(raw or "").split())  # collapse whitespace, drop control newlines/tabs
    if not text or text.lower() == "nan":
        if default is not None:
            return default
        raise ValueError(f"{field} is required")
    return text[:max_len]


def check_file_signature(ext, data):
    """Check the file's real content matches its extension (a renamed .exe is rejected)."""
    if ext == ".xlsx":
        if not data.startswith(b"PK\x03\x04"):
            raise ValueError("The file is not a real Excel (.xlsx) file")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
                names = {i.filename for i in infos}
                total = sum(i.file_size for i in infos)
                ratio = max((i.file_size / max(i.compress_size, 1) for i in infos), default=0)
        except zipfile.BadZipFile:
            raise ValueError("The Excel file is damaged")
        if "[Content_Types].xml" not in names or not any(n.startswith("xl/") for n in names):
            raise ValueError("The file is not a real Excel (.xlsx) file")
        if total > MAX_UNZIPPED_BYTES or ratio > MAX_ZIP_RATIO:
            raise ValueError("The Excel file expands to an unsafe size (possible zip bomb)")
    else:  # .csv must be plain text
        head = data[:4096]
        if b"\x00" in head or head.startswith((b"PK", b"MZ", b"%PDF", b"\x7fELF")):
            raise ValueError("The file is not a plain-text CSV file")


def is_blank(value):
    return value is None or (isinstance(value, float) and value != value) or str(value).strip().lower() in ("", "nan")


def parse_days(raw, field):
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number of days")
    if value != value or value < 0 or value > 3650:
        raise ValueError(f"{field} must be between 0 and 3,650 days")
    return round(value, 2)


def parse_pert(rec):
    """Optional PERT fields. Each three-point estimate must be complete and ordered O <= M <= P."""
    out = {k: None for k in PERT_FIELDS}
    code = rec.get("code")
    if not is_blank(code):
        code = str(code).strip()
        if not CODE_RE.match(code):
            raise ValueError("ID may only use letters, numbers, dot, dash or underscore (max 20)")
        out["code"] = code
    for prefix, label, parser in (("cost", "cost", parse_amount), ("dur", "days", parse_days)):
        raw = [rec.get(f"{prefix}_{x}") for x in "omp"]
        given = [not is_blank(v) for v in raw]
        if not any(given):
            continue
        if not all(given):
            raise ValueError(f"PERT {label} needs all three values: optimistic, most likely and pessimistic")
        o, m, p = (parser(v, f"{n} {label}") for v, n in zip(raw, ("Optimistic", "Most likely", "Pessimistic")))
        if not o <= m <= p:
            raise ValueError(f"PERT {label} must satisfy optimistic <= most likely <= pessimistic")
        if prefix == "dur" and p <= 0:
            raise ValueError("PERT days must be greater than 0")
        out[f"{prefix}_o"], out[f"{prefix}_m"], out[f"{prefix}_p"] = o, m, p
    preds = rec.get("predecessors")
    if not is_blank(preds):
        from pert import split_predecessors
        parts = split_predecessors(str(preds))
        if len(parts) > 20 or not all(CODE_RE.match(x) for x in parts):
            raise ValueError("Predecessors must be a list of activity IDs, e.g. 1.1, 1.2")
        if out["dur_o"] is None:
            raise ValueError("Predecessors need PERT days on the same row")
        out["predecessors"] = ", ".join(parts)
    return out


def insert_items(db, project_id, rows):
    db.executemany(
        f"INSERT INTO cost_items (project_id, {', '.join(ITEM_COLUMNS)}) "
        f"VALUES (?, {', '.join('?' for _ in ITEM_COLUMNS)})",
        [(project_id, *(r.get(c) for c in ITEM_COLUMNS)) for r in rows],
    )


def detect_delimiter(text):
    """Comma, semicolon (European Excel), tab or pipe separated files are all accepted."""
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_upload(file_storage):
    """Read an uploaded CSV/XLSX into a list of validated row dicts."""
    ext = Path(file_storage.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Only .csv and .xlsx files are allowed")
    data = file_storage.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File is larger than 2 MB")
    if not data:
        raise ValueError("The file is empty")
    check_file_signature(ext, data)

    try:
        if ext == ".csv":
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1252", errors="replace")  # Excel's default on Windows
            df = pd.read_csv(io.StringIO(text), dtype=str, nrows=MAX_ROWS + 1, sep=detect_delimiter(text))
        else:
            df = pd.read_excel(io.BytesIO(data), dtype=str, engine="openpyxl", nrows=MAX_ROWS + 1)
    except Exception:
        raise ValueError("The file could not be read. Is it a valid CSV/Excel file?")

    # Map whatever headers the user used onto our four columns.
    rename = {}
    for col in df.columns:
        key = " ".join(re.sub(r"[^a-z0-9]+", " ", str(col).lower()).split())
        for target, aliases in COLUMN_ALIASES.items():
            if key in aliases:
                rename[col] = target
    df = df.rename(columns=rename)

    missing = {"item", "estimated", "actual"} - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required column(s): " + ", ".join(sorted(missing))
            + ". Download the sample template to see the expected format."
        )
    df = df.dropna(how="all")
    if df.empty:
        raise ValueError("The file has no data rows")
    if len(df) > MAX_ROWS:
        raise ValueError(f"Too many rows (max {MAX_ROWS})")

    rows, errors = [], []
    for i, rec in enumerate(df.to_dict("records"), start=2):  # row 1 = header
        try:
            rows.append({
                "item": clean_text(rec.get("item"), "Item"),
                "category": clean_text(rec.get("category"), "Category", 50, "General"),
                "estimated": parse_amount(rec.get("estimated"), "Estimated cost"),
                "actual": parse_amount(rec.get("actual"), "Actual cost"),
                **parse_pert(rec),
            })
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
    if errors:
        shown = errors[:5]
        more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        raise ValueError("; ".join(shown) + more)
    return rows


# ---------- account routes ----------
@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", key_func=get_remote_address, methods=["POST"])
def register():
    if current_user_id():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        error = None
        if not USERNAME_RE.match(username):
            error = "Username must be 3-30 characters: letters, numbers, dot, dash or underscore"
        elif len(password) < 8 or len(password) > 128:
            error = "Password must be 8-128 characters long"
        elif password.lower() == username.lower():
            error = "Password must not be the same as the username"
        elif password != confirm:
            error = "Passwords do not match"
        if error is None:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                error = "That username is taken"
            else:
                session.clear()  # new session id: prevents session fixation
                session.permanent = True
                session["user_id"], session["username"] = cur.lastrowid, username
                flash(f"Welcome, {username}! Your account was created.", "success")
                return redirect(url_for("index"))
        flash(error, "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", key_func=get_remote_address, methods=["POST"])
def login():
    if current_user_id():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
        if user and ok:
            session.clear()
            session.permanent = True
            session["user_id"], session["username"] = user["id"], user["username"]
            return redirect(safe_next(request.args.get("next")))
        log_event("LOGIN_FAILED", attempted_username=username[:40])
        flash("Invalid username or password", "error")  # same message either way
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out", "info")
    return redirect(url_for("login"))


# ---------- project routes ----------
@app.route("/")
@login_required
def index():
    projects = get_db().execute(
        """SELECT p.id, p.name, p.created_at, COUNT(c.id) AS item_count
           FROM projects p LEFT JOIN cost_items c ON c.project_id = p.id
           WHERE p.owner_id = ?
           GROUP BY p.id ORDER BY p.id DESC""",
        (current_user_id(),),
    ).fetchall()
    return render_template("index.html", projects=projects)


@app.post("/projects")
@login_required
@limiter.limit("30 per hour")
def create_project():
    try:
        name = clean_text(request.form.get("name"), "Project name", 80)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("index"))
    db = get_db()
    cur = db.execute("INSERT INTO projects (name, owner_id) VALUES (?, ?)", (name, current_user_id()))
    db.commit()
    return redirect(url_for("project_detail", project_id=cur.lastrowid))


@app.post("/projects/<int:project_id>/delete")
@login_required
def delete_project(project_id):
    project = get_project_or_404(project_id)
    db = get_db()
    db.execute("DELETE FROM projects WHERE id = ? AND owner_id = ?", (project_id, current_user_id()))
    db.commit()
    flash(f"Deleted project “{project['name']}”", "info")
    return redirect(url_for("index"))


@app.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = get_project_or_404(project_id)
    analysis = load_analysis(project_id)
    anomalies = detect_anomalies(analysis["lines"])
    summary = get_db().execute(
        "SELECT * FROM ai_summaries WHERE project_id = ?", (project_id,)
    ).fetchone()
    summary_stale = summary is not None and summary["data_hash"] != data_hash(analysis)
    pert_result = pert.analyse(analysis["lines"], project["target_days"])
    return render_template("project.html", project=project, a=analysis, pert=pert_result,
                           chart_data=build_chart_data(analysis, pert_result),
                           anomalies=anomalies, summary=summary, summary_stale=summary_stale)


def load_analysis(project_id):
    items = get_db().execute(
        "SELECT * FROM cost_items WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return analyse_project(items)


def data_hash(analysis):
    """Fingerprint of the cost data, used to tell when a saved AI summary is out of date."""
    key = [[l.get(c) for c in ITEM_COLUMNS] for l in analysis["lines"]]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def report_inputs(project_id):
    project = get_project_or_404(project_id)
    analysis = load_analysis(project_id)
    if not analysis["lines"]:
        return project, None, None, None, None
    anomalies = detect_anomalies(analysis["lines"])
    row = get_db().execute("SELECT * FROM ai_summaries WHERE project_id = ?", (project_id,)).fetchone()
    summary = None
    if row:
        summary = dict(row)
        summary["stale"] = row["data_hash"] != data_hash(analysis)
    return project, analysis, anomalies, summary, pert.analyse(analysis["lines"], project["target_days"])


def _download(data, mimetype, filename):
    resp = Response(data, mimetype=mimetype)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.route("/projects/<int:project_id>/report.pdf")
@login_required
@limiter.limit("10 per minute")
def report_pdf(project_id):
    project, analysis, anomalies, summary, pert_result = report_inputs(project_id)
    if analysis is None:
        flash("Add some cost items before downloading a report", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    pdf = build_pdf(project["name"], analysis, anomalies, summary, pert_result)
    return _download(pdf, "application/pdf", safe_filename(project["name"], "pdf"))


@app.route("/projects/<int:project_id>/report.xlsx")
@login_required
@limiter.limit("10 per minute")
def report_xlsx(project_id):
    project, analysis, anomalies, summary, pert_result = report_inputs(project_id)
    if analysis is None:
        flash("Add some cost items before exporting", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    data = build_xlsx(project["name"], analysis, anomalies, summary, pert_result)
    return _download(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     safe_filename(project["name"], "xlsx"))


@app.post("/projects/<int:project_id>/ai-summary")
@login_required
@limiter.limit("5 per minute;30 per day")
def ai_summary(project_id):
    project = get_project_or_404(project_id)
    analysis = load_analysis(project_id)
    if not analysis["lines"]:
        flash("Add some cost items first", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    anomalies = detect_anomalies(analysis["lines"])
    text, source = generate_summary(project["name"], analysis, anomalies)
    db = get_db()
    db.execute(
        """INSERT INTO ai_summaries (project_id, summary, source, data_hash, generated_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(project_id) DO UPDATE SET summary = excluded.summary,
             source = excluded.source, data_hash = excluded.data_hash,
             generated_at = excluded.generated_at""",
        (project_id, text, source, data_hash(analysis)),
    )
    db.commit()
    flash(f"Summary generated ({source})", "success" if source.startswith("Groq") else "info")
    return redirect(url_for("project_detail", project_id=project_id) + "#ai")


def build_chart_data(analysis, pert_result=None):
    """Only the fields the charts need (keeps database ids out of the page)."""
    pert_result = pert_result or {}
    cost, sched = pert_result.get("cost"), pert_result.get("schedule")
    if sched and "error" in sched:
        sched = None
    pert_data = {
        "cost": {k: cost[k] for k in ("expected", "sd", "budget", "actual")} if cost and cost["sd"] > 0 else None,
        "schedule": {
            "duration": sched["duration"], "sd": sched["sd"], "target": sched["target_days"],
            "activities": [{"code": a["code"], "item": a["item"], "es": a["es"], "ef": a["ef"],
                            "slack": a["slack"], "critical": a["critical"]} for a in sched["activities"]],
        } if sched else None,
    }
    return {
        "pert": pert_data,
        "categories": [
            {"category": c["category"], "estimated": c["estimated"], "actual": c["actual"]}
            for c in sorted(analysis["categories"], key=lambda c: c["actual"], reverse=True)
        ],
        "items": [
            {"item": r["item"], "variance": r["variance"], "pct_variance": r["pct_variance"],
             "status": r["status"], "status_key": status_class(r["status"])}
            for r in analysis["lines"]
        ],
    }


@app.post("/projects/<int:project_id>/items")
@login_required
@limiter.limit("60 per minute")
def add_item(project_id):
    get_project_or_404(project_id)
    f = request.form
    try:
        row = {
            "item": clean_text(f.get("item"), "Item"),
            "category": clean_text(f.get("category"), "Category", 50, "General"),
            "estimated": parse_amount(f.get("estimated"), "Estimated cost"),
            "actual": parse_amount(f.get("actual"), "Actual cost"),
            **parse_pert({k: f.get(k) for k in PERT_FIELDS}),
        }
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("project_detail", project_id=project_id))
    count = get_db().execute("SELECT COUNT(*) FROM cost_items WHERE project_id = ?", (project_id,)).fetchone()[0]
    if count >= MAX_ROWS:
        flash(f"A project can hold at most {MAX_ROWS} items", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    db = get_db()
    insert_items(db, project_id, [row])
    db.commit()
    flash("Item added", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.post("/projects/<int:project_id>/items/<int:item_id>/delete")
@login_required
def delete_item(project_id, item_id):
    get_project_or_404(project_id)
    db = get_db()
    db.execute("DELETE FROM cost_items WHERE id = ? AND project_id = ?", (item_id, project_id))
    db.commit()
    return redirect(url_for("project_detail", project_id=project_id))


@app.post("/projects/<int:project_id>/upload")
@login_required
@limiter.limit("10 per minute")
def upload(project_id):
    get_project_or_404(project_id)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a file to upload", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    try:
        rows = read_upload(file)
    except ValueError as e:
        log_event("UPLOAD_REJECTED", filename=(file.filename or "")[:80], reason=str(e)[:120])
        flash(f"Upload rejected: {e}", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    db = get_db()
    if request.form.get("mode") == "replace":
        db.execute("DELETE FROM cost_items WHERE project_id = ?", (project_id,))
    else:
        count = db.execute("SELECT COUNT(*) FROM cost_items WHERE project_id = ?", (project_id,)).fetchone()[0]
        if count + len(rows) > MAX_ROWS:
            flash(f"Upload rejected: a project can hold at most {MAX_ROWS} items", "error")
            return redirect(url_for("project_detail", project_id=project_id))
    insert_items(db, project_id, rows)
    db.commit()
    flash(f"Imported {len(rows)} item(s)", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.post("/projects/<int:project_id>/target")
@login_required
def set_target(project_id):
    get_project_or_404(project_id)
    raw = (request.form.get("target_days") or "").strip()
    try:
        target = None if raw == "" else parse_days(raw, "Target duration")
        if target is not None and target <= 0:
            raise ValueError("Target duration must be greater than 0 days")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("project_detail", project_id=project_id) + "#pert")
    db = get_db()
    db.execute("UPDATE projects SET target_days = ? WHERE id = ? AND owner_id = ?",
               (target, project_id, current_user_id()))
    db.commit()
    flash("Target duration saved" if target else "Target duration cleared", "success")
    return redirect(url_for("project_detail", project_id=project_id) + "#pert")


@app.route("/sample-pert.csv")
def sample_pert():
    return send_from_directory(BASE_DIR / "static", "sample_pert.csv", as_attachment=True)


@app.route("/sample-template.csv")
def sample_template():
    return send_from_directory(BASE_DIR / "static", "sample_costs.csv", as_attachment=True)


@app.route("/healthz")
@limiter.exempt
def healthz():
    return {"status": "ok"}


# ---------- template filters ----------
@app.template_filter("money")
def money(value):
    return f"{value:,.2f}"


@app.template_filter("signed")
def signed(value):
    return f"{value:+,.2f}"


@app.template_filter("pct")
def pct(value):
    return "n/a" if value is None else f"{value:+.1f}%"


@app.template_filter("status_class")
def status_class(status):
    return {"Over budget": "over", "Under budget": "under"}.get(status, "on")


# ---------- security headers ----------
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


@app.after_request
def security_headers(resp):
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if IS_PROD:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if not request.path.startswith("/static/"):
        h["Cache-Control"] = "no-store"  # pages and reports hold private cost data
    h.pop("Server", None)
    return resp


# ---------- error pages (never show stack traces) ----------
def error_page(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(404)
def not_found(_e):
    return error_page(404, "Not found", "This page or project does not exist, or you don't have access to it.")


@app.errorhandler(405)
def method_not_allowed(_e):
    return error_page(405, "Not allowed", "That action isn't allowed here.")


@app.errorhandler(CSRFError)
def csrf_error(e):
    log_event("CSRF_REJECTED", reason=e.description)
    return error_page(400, "Request blocked", "Your form expired or did not come from this site. Go back, refresh the page and try again.")


@app.errorhandler(429)
def too_many_requests(e):
    log_event("RATE_LIMITED", limit=str(e.description))
    return error_page(429, "Too many requests", f"You're doing that too often ({e.description}). Please wait and try again.")


@app.errorhandler(413)
def too_large(_e):
    log_event("UPLOAD_TOO_LARGE")
    flash("Upload rejected: file is larger than 2 MB", "error")
    m = re.fullmatch(r"/projects/(\d+)/upload", request.path)  # never trust the Referer header
    return redirect(url_for("project_detail", project_id=int(m.group(1))) if m else url_for("index"))


@app.errorhandler(500)
def server_error(_e):
    return error_page(500, "Something went wrong", "An unexpected error occurred. Please try again.")


if __name__ == "__main__":
    # Debug mode exposes an interactive console that can run code on the server,
    # so it is only ever enabled explicitly, and never in production.
    debug = os.environ.get("FLASK_DEBUG") == "1" and not IS_PROD
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=debug)
