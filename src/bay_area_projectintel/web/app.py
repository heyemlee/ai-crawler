"""Operator web dashboard for the ProjectIntel pipeline.

A deliberately small FastAPI app so a non-technical operator can, from a browser:
  * click "开始跑批" to run the full pipeline (fetch → … → export → email),
  * download / re-send the latest leads spreadsheet,
  * configure who the spreadsheet is emailed to (and the subject).

It does NOT expose source management or lead browsing — that stays in the CLI/MCP.
The long-running pipeline is run as a background subprocess (the existing CLI), so a
request never blocks; the page polls ``/status`` for progress and a log tail.

Run locally:  ``projectintel-web``  (honours ``PORT``, default 8000)
Protect a public deployment by setting ``OPERATOR_PASSWORD`` — then every page needs
HTTP Basic auth (any username, that password).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from bay_area_projectintel.config import load_config
from bay_area_projectintel.notify import parse_recipients

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _source_flags() -> list[str]:
    """The `-s name` flags for the run. Override with PROJECTINTEL_SOURCES (space-separated)."""
    env = os.environ.get("PROJECTINTEL_SOURCES")
    if env:
        return env.split()
    return [
        "-s", "datasf-building-permits",
        "-s", "marin-building-permits",
        "-s", "sanjose-active-building-permits",
        "-s", "sunnyvale-energov-permits",
    ]


class ConfigStore:
    """Operator-editable settings (recipient, subject) persisted as JSON on the volume.

    Env values seed the defaults; the file, once written, wins — so an operator can
    change the recipient at runtime without redeploying.
    """

    def __init__(self, path: Path, defaults: dict[str, Any]):
        self.path = Path(path)
        self.defaults = defaults

    def load(self) -> dict[str, Any]:
        data = dict(self.defaults)
        if self.path.exists():
            try:
                data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return data

    def save(self, **fields: Any) -> dict[str, Any]:
        data = self.load()
        for key, value in fields.items():
            if value is not None:
                data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data


class Runner:
    """Runs the pipeline (then the email step) in a background thread, once at a time."""

    def __init__(self, *, out_path: Path, log_path: Path, config: ConfigStore):
        self.out_path = Path(out_path)
        self.log_path = Path(log_path)
        self.config = config
        self._lock = threading.Lock()
        self._state = "idle"  # idle | running | success | failed
        self._started_at: str | None = None
        self._finished_at: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "log_tail": self._tail(),
            }

    def _tail(self, n: int = 120) -> str:
        try:
            return "\n".join(self.log_path.read_text(encoding="utf-8").splitlines()[-n:])
        except OSError:
            return ""

    def start(self, *, email: bool = True) -> bool:
        """Kick off a run. Returns False if one is already in flight."""
        with self._lock:
            if self._state == "running":
                return False
            self._state = "running"
            self._started_at = _now()
            self._finished_at = None
        threading.Thread(target=self._run, kwargs={"email": email}, daemon=True).start()
        return True

    def _exec(self, args: list[str], log) -> bool:
        log.write(f"\n$ {' '.join(args)}\n")
        log.flush()
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=False)
        return proc.returncode == 0

    def _run(self, *, email: bool) -> None:
        cli = [sys.executable, "-m", "bay_area_projectintel.cli"]
        ok = True
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.log_path.open("w", encoding="utf-8") as log:
                log.write(f"[{_now()}] run started\n")
                ok = self._exec(
                    [*cli, "run", *_source_flags(), "--out", str(self.out_path)], log
                )
                if ok and email:
                    ok = self._exec(self._email_args(cli), log)
                log.write(f"\n[{_now()}] {'OK' if ok else 'FAILED'}\n")
        except Exception as exc:  # noqa: BLE001
            ok = False
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[{_now()}] runner crashed: {type(exc).__name__}: {exc}\n")
        finally:
            with self._lock:
                self._state = "success" if ok else "failed"
                self._finished_at = _now()

    def _email_args(self, cli: list[str]) -> list[str]:
        cfg = self.config.load()
        args = [*cli, "email", "--attach", str(self.out_path)]
        if cfg.get("email_to"):
            args += ["--to", cfg["email_to"]]
        if cfg.get("email_subject"):
            args += ["--subject", cfg["email_subject"]]
        return args

    def send_latest(self) -> bool:
        """Email the latest spreadsheet now, without re-running the pipeline."""
        with self._lock:
            if self._state == "running":
                return False
        cli = [sys.executable, "-m", "bay_area_projectintel.cli"]
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{_now()}] re-send latest\n")
            return self._exec(self._email_args(cli), log)


# --- App wiring ---------------------------------------------------------------

_config = load_config()
_settings = _config.settings
_state_dir = _settings.db_path.parent
_out_path = Path(os.environ.get("PROJECTINTEL_OUT", str(_state_dir / "leads.xlsx")))
_config_store = ConfigStore(
    _state_dir / "operator-config.json",
    defaults={"email_to": _settings.email_to or "", "email_subject": ""},
)
_runner = Runner(
    out_path=_out_path,
    log_path=_state_dir / "web-run.log",
    config=_config_store,
)

app = FastAPI(title="ProjectIntel 控制台")
_security = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """HTTP Basic gate. No-op unless OPERATOR_PASSWORD is set (so local use is open)."""
    password = os.environ.get("OPERATOR_PASSWORD")
    if not password:
        return
    if credentials is None or not secrets.compare_digest(credentials.password, password):
        raise HTTPException(
            status_code=401,
            detail="需要密码",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Unauthenticated health check for Railway."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    cfg = _config_store.load()
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "email_to": cfg.get("email_to", ""),
            "email_subject": cfg.get("email_subject", ""),
            "has_excel": _settings.latest_excel_path.exists() or _out_path.exists(),
        },
    )


@app.get("/status")
def status(_: None = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(_runner.status())


@app.post("/run")
def run(_: None = Depends(require_auth)) -> JSONResponse:
    started = _runner.start(email=True)
    return JSONResponse({"started": started, **_runner.status()})


@app.post("/config")
def save_config(
    email_to: str = Form(""),
    email_subject: str = Form(""),
    _: None = Depends(require_auth),
) -> RedirectResponse:
    # Normalize the recipient list so a typo'd separator doesn't silently drop people.
    recipients = ", ".join(parse_recipients(email_to))
    _config_store.save(email_to=recipients, email_subject=email_subject.strip())
    return RedirectResponse("/?saved=1", status_code=303)


@app.post("/send-latest")
def send_latest(_: None = Depends(require_auth)) -> JSONResponse:
    ok = _runner.send_latest()
    return JSONResponse({"ok": ok})


@app.get("/download")
def download(_: None = Depends(require_auth)) -> FileResponse:
    path = _settings.latest_excel_path if _settings.latest_excel_path.exists() else _out_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="还没有生成表格，请先跑一次批。")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="leads.xlsx",
    )


def main() -> None:
    """Console entrypoint — serve the dashboard (honours PORT, default 8000)."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
