"""Test setup: a throwaway database and a fresh rate-limit store for every test."""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Must be set before the app module is imported.
_tmpdir = tempfile.mkdtemp(prefix="cost-analyzer-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_tmpdir, "test.db")
os.environ["APP_ENV"] = "development"
os.environ.pop("GROQ_API_KEY", None)  # never call the real AI service from tests

import app as app_module  # noqa: E402

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
PASSWORD = "Test-pass-123"


@pytest.fixture()
def app():
    flask_app = app_module.app
    flask_app.config.update(TESTING=True)
    with flask_app.app_context():
        db = app_module.get_db()
        db.executescript("DELETE FROM ai_summaries; DELETE FROM cost_items; DELETE FROM projects; DELETE FROM users;")
        db.commit()
    app_module.limiter.reset()
    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_client(app):
    """Factory for independent browsers (separate cookie jars)."""
    return lambda: app.test_client()


def csrf_token(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    m = CSRF_RE.search(html)
    assert m, f"no CSRF token on {path}"
    return m.group(1)


def register(client, username, password=PASSWORD):
    token = csrf_token(client, "/register")
    return client.post("/register", data={"username": username, "password": password,
                                          "confirm": password, "csrf_token": token})


def login(client, username, password=PASSWORD):
    token = csrf_token(client, "/login")
    return client.post("/login", data={"username": username, "password": password, "csrf_token": token})


def create_project(client, name="Test project"):
    token = csrf_token(client, "/")
    r = client.post("/projects", data={"name": name, "csrf_token": token})
    assert r.status_code == 302, r.status_code
    return int(r.headers["Location"].rstrip("/").rsplit("/", 1)[1])


def post_form(client, path, data, page=None, **kwargs):
    """POST with a valid CSRF token taken from `page` (defaults to the project page)."""
    page = page or re.sub(r"(/projects/\d+).*", r"\1", path)
    data = dict(data, csrf_token=csrf_token(client, page))
    return client.post(path, data=data, **kwargs)
