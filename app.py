"""Cost Analyzer - Flask app: data entry, calculations, charts, AI insights."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, url_for)

from ai import detect_anomalies, generate_summary
from calculations import analyse_project
from report import build_pdf, build_xlsx, safe_filename

BASE_DIR = Path(__file__).resolve().parent

# Load settings (e.g. GROQ_API_KEY) from a local .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DB_PATH = BASE_DIR / "instance" / "costs.db"
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
MAX_ROWS = 1000

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB upload limit

# Header names we accept from uploaded files, mapped to our internal columns.
COLUMN_ALIASES = {
    "item": {"item", "description", "item name", "expense", "name"},
    "category": {"category", "type", "group"},
    "estimated": {"estimated", "estimated cost", "estimate", "budget", "planned"},
    "actual": {"actual", "actual cost", "spent", "actual spend"},
}


# ---------- database ----------
def get_db():
    if "db" not in g:
        DB_PATH.parent.mkdir(exist_ok=True)
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
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
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
    db.commit()


with app.app_context():
    init_db()


def get_project_or_404(project_id):
    project = get_db().execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if project is None:
        abort(404)
    return project


# ---------- validation helpers ----------
def parse_amount(raw, field):
    """Turn user input like '1,200.50' into a non-negative float."""
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")
    if value < 0 or value > 1e12:
        raise ValueError(f"{field} must be between 0 and 1,000,000,000,000")
    return round(value, 2)


def clean_text(raw, field, max_len=100, default=None):
    text = str(raw or "").strip()
    if not text:
        if default is not None:
            return default
        raise ValueError(f"{field} is required")
    return text[:max_len]


def read_upload(file_storage):
    """Read an uploaded CSV/XLSX into a list of validated row dicts."""
    ext = Path(file_storage.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Only .csv and .xlsx files are allowed")

    try:
        if ext == ".csv":
            df = pd.read_csv(file_storage, dtype=str)
        else:
            df = pd.read_excel(file_storage, dtype=str, engine="openpyxl")
    except Exception:
        raise ValueError("The file could not be read. Is it a valid CSV/Excel file?")

    # Map whatever headers the user used onto our four columns.
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower().replace("_", " ")
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
            })
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
    if errors:
        shown = errors[:5]
        more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        raise ValueError("; ".join(shown) + more)
    return rows


# ---------- routes ----------
@app.route("/")
def index():
    projects = get_db().execute(
        """SELECT p.id, p.name, p.created_at, COUNT(c.id) AS item_count
           FROM projects p LEFT JOIN cost_items c ON c.project_id = p.id
           GROUP BY p.id ORDER BY p.id DESC"""
    ).fetchall()
    return render_template("index.html", projects=projects)


@app.post("/projects")
def create_project():
    try:
        name = clean_text(request.form.get("name"), "Project name", 80)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("index"))
    db = get_db()
    cur = db.execute("INSERT INTO projects (name) VALUES (?)", (name,))
    db.commit()
    return redirect(url_for("project_detail", project_id=cur.lastrowid))


@app.route("/projects/<int:project_id>")
def project_detail(project_id):
    project = get_project_or_404(project_id)
    items = get_db().execute(
        "SELECT * FROM cost_items WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    analysis = analyse_project(items)
    anomalies = detect_anomalies(analysis["lines"])
    summary = get_db().execute(
        "SELECT * FROM ai_summaries WHERE project_id = ?", (project_id,)
    ).fetchone()
    summary_stale = summary is not None and summary["data_hash"] != data_hash(analysis)
    return render_template("project.html", project=project, a=analysis,
                           chart_data=build_chart_data(analysis),
                           anomalies=anomalies, summary=summary, summary_stale=summary_stale)


def load_analysis(project_id):
    items = get_db().execute(
        "SELECT * FROM cost_items WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return analyse_project(items)


def data_hash(analysis):
    """Fingerprint of the cost data, used to tell when a saved AI summary is out of date."""
    key = [(l["item"], l["category"], l["estimated"], l["actual"]) for l in analysis["lines"]]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def report_inputs(project_id):
    project = get_project_or_404(project_id)
    analysis = load_analysis(project_id)
    if not analysis["lines"]:
        return project, None, None, None
    anomalies = detect_anomalies(analysis["lines"])
    row = get_db().execute("SELECT * FROM ai_summaries WHERE project_id = ?", (project_id,)).fetchone()
    summary = None
    if row:
        summary = dict(row)
        summary["stale"] = row["data_hash"] != data_hash(analysis)
    return project, analysis, anomalies, summary


def _download(data, mimetype, filename):
    resp = Response(data, mimetype=mimetype)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/projects/<int:project_id>/report.pdf")
def report_pdf(project_id):
    project, analysis, anomalies, summary = report_inputs(project_id)
    if analysis is None:
        flash("Add some cost items before downloading a report", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    pdf = build_pdf(project["name"], analysis, anomalies, summary)
    return _download(pdf, "application/pdf", safe_filename(project["name"], "pdf"))


@app.route("/projects/<int:project_id>/report.xlsx")
def report_xlsx(project_id):
    project, analysis, anomalies, summary = report_inputs(project_id)
    if analysis is None:
        flash("Add some cost items before exporting", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    data = build_xlsx(project["name"], analysis, anomalies, summary)
    return _download(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     safe_filename(project["name"], "xlsx"))


@app.post("/projects/<int:project_id>/ai-summary")
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


def build_chart_data(analysis):
    """Only the fields the charts need (keeps database ids out of the page)."""
    return {
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
def add_item(project_id):
    get_project_or_404(project_id)
    f = request.form
    try:
        row = (
            project_id,
            clean_text(f.get("item"), "Item"),
            clean_text(f.get("category"), "Category", 50, "General"),
            parse_amount(f.get("estimated"), "Estimated cost"),
            parse_amount(f.get("actual"), "Actual cost"),
        )
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("project_detail", project_id=project_id))
    db = get_db()
    db.execute(
        "INSERT INTO cost_items (project_id, item, category, estimated, actual) VALUES (?,?,?,?,?)",
        row,
    )
    db.commit()
    flash("Item added", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.post("/projects/<int:project_id>/items/<int:item_id>/delete")
def delete_item(project_id, item_id):
    get_project_or_404(project_id)
    db = get_db()
    db.execute("DELETE FROM cost_items WHERE id = ? AND project_id = ?", (item_id, project_id))
    db.commit()
    return redirect(url_for("project_detail", project_id=project_id))


@app.post("/projects/<int:project_id>/upload")
def upload(project_id):
    get_project_or_404(project_id)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a file to upload", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    try:
        rows = read_upload(file)
    except ValueError as e:
        flash(f"Upload rejected: {e}", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    db = get_db()
    if request.form.get("mode") == "replace":
        db.execute("DELETE FROM cost_items WHERE project_id = ?", (project_id,))
    db.executemany(
        "INSERT INTO cost_items (project_id, item, category, estimated, actual) VALUES (?,?,?,?,?)",
        [(project_id, r["item"], r["category"], r["estimated"], r["actual"]) for r in rows],
    )
    db.commit()
    flash(f"Imported {len(rows)} item(s)", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/sample-template.csv")
def sample_template():
    return send_from_directory(BASE_DIR / "static", "sample_costs.csv", as_attachment=True)


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


@app.errorhandler(413)
def too_large(_e):
    flash("Upload rejected: file is larger than 2 MB", "error")
    return redirect(request.referrer or url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
