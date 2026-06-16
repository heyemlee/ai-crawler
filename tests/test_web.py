import importlib
import os
import tempfile

# Point all state at a temp dir BEFORE importing the app (it resolves paths at import).
_TMP = tempfile.mkdtemp()
os.environ["PROJECTINTEL_DB_PATH"] = os.path.join(_TMP, "db.sqlite3")

import pytest
from fastapi.testclient import TestClient

from bay_area_projectintel.web import app as webapp

client = TestClient(webapp.app)


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    # Default: auth disabled (local-style). Individual tests opt back in.
    monkeypatch.delenv("OPERATOR_PASSWORD", raising=False)


def test_healthz_is_open():
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_index_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "ProjectIntel" in r.text
    assert "开始跑批" in r.text


def test_status_starts_idle():
    assert client.get("/status").json()["state"] == "idle"


def test_config_save_roundtrip():
    r = client.post(
        "/config",
        data={"email_to": "a@x.com ; b@y.com", "email_subject": "本周线索"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cfg = webapp._config_store.load()
    # Recipients normalized to a clean comma list regardless of input separators.
    assert cfg["email_to"] == "a@x.com, b@y.com"
    assert cfg["email_subject"] == "本周线索"
    # And the saved values are reflected back in the page.
    assert "a@x.com, b@y.com" in client.get("/").text


def test_email_args_uses_saved_config():
    webapp._config_store.save(email_to="z@x.com", email_subject="Hi")
    args = webapp._runner._email_args(["py", "-m", "cli"])
    assert "--to" in args and "z@x.com" in args
    assert "--subject" in args and "Hi" in args
    assert "--attach" in args


def test_run_endpoint_starts(monkeypatch):
    calls = {}
    monkeypatch.setattr(webapp._runner, "start", lambda email=True: calls.setdefault("started", True))
    r = client.post("/run")
    assert r.status_code == 200
    assert calls.get("started") is True


def test_download_404_when_no_excel(monkeypatch, tmp_path):
    missing = tmp_path / "nope.xlsx"
    monkeypatch.setattr(webapp, "_out_path", missing)
    monkeypatch.setattr(webapp._settings, "latest_excel_path", missing)
    assert client.get("/download").status_code == 404


def test_auth_enforced_when_password_set(monkeypatch):
    monkeypatch.setenv("OPERATOR_PASSWORD", "s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("op", "wrong")).status_code == 401
    assert client.get("/", auth=("op", "s3cret")).status_code == 200
    # Health check stays open even with auth on.
    assert client.get("/healthz").status_code == 200


def test_email_setup_page_renders():
    r = client.get("/email-setup")
    assert r.status_code == 200
    assert "应用专用密码" in r.text
    assert "Microsoft 365" in r.text


def test_config_saves_smtp_fields():
    r = client.post(
        "/config",
        data={
            "smtp_host": "smtp.office365.com", "smtp_port": "587", "smtp_use_ssl": "false",
            "smtp_user": "u@ravv.com", "smtp_password": "secret", "email_to": "to@x.com",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    cfg = webapp._config_store.load()
    assert cfg["smtp_host"] == "smtp.office365.com"
    assert cfg["smtp_port"] == "587"
    assert cfg["smtp_use_ssl"] == "false"
    assert cfg["smtp_user"] == "u@ravv.com"
    assert cfg["smtp_password"] == "secret"
    assert cfg["email_from"] == "u@ravv.com"  # defaults to smtp_user when blank
    # The injected subprocess env carries the saved SMTP through to the CLI.
    env = webapp._runner._subprocess_env()
    assert env["PROJECTINTEL_SMTP_USER"] == "u@ravv.com"
    assert env["PROJECTINTEL_SMTP_PASSWORD"] == "secret"


def test_config_blank_password_keeps_existing():
    webapp._config_store.save(smtp_password="keepme")
    client.post(
        "/config",
        data={"smtp_user": "u@x.com", "smtp_password": "", "email_to": "t@x.com"},
        follow_redirects=False,
    )
    assert webapp._config_store.load()["smtp_password"] == "keepme"


def test_test_email_needs_config():
    webapp._config_store.save(smtp_user="", smtp_password="", email_to="")
    assert client.post("/test-email").json()["ok"] is False


def test_test_email_success(monkeypatch):
    webapp._config_store.save(smtp_user="u@x.com", smtp_password="p", email_to="to@x.com")

    class FakeChannel:
        def send(self, note):
            pass

    monkeypatch.setattr(webapp, "_channel_from_config", lambda cfg, recipients: FakeChannel())
    assert client.post("/test-email").json()["ok"] is True
