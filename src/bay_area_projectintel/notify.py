from __future__ import annotations

import mimetypes
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Protocol, Sequence

from bay_area_projectintel.report import build_report


@dataclass(frozen=True)
class Notification:
    subject: str
    body: str
    # Files to attach when the channel supports it (e.g. EmailChannel). Other
    # channels (stdout/file) ignore attachments.
    attachments: tuple[Path, ...] = field(default_factory=tuple)


class NotificationChannel(Protocol):
    name: str

    def send(self, note: Notification) -> None: ...


class StdoutChannel:
    """Default local channel. OpenClaw will later add a WeChat channel alongside this."""

    name = "stdout"

    def __init__(self, printer=print):
        self._print = printer

    def send(self, note: Notification) -> None:
        self._print(f"{note.subject}\n{note.body}")


class FileChannel:
    """Appends timestamped notifications to a log so unattended runs leave a trail."""

    name = "file"

    def __init__(self, path: Path):
        self.path = Path(path)

    def send(self, note: Notification) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {note.subject}\n{note.body}\n\n")


def parse_recipients(raw: str | None) -> tuple[str, ...]:
    """Split a comma/semicolon/whitespace-separated recipient string into a tuple.

    Accepts ``"a@x.com, b@y.com"`` or newline/semicolon separated lists and drops
    blanks, so a single env var can hold one or many recipients.
    """
    if not raw:
        return ()
    parts = raw.replace(";", ",").replace("\n", ",").split(",")
    return tuple(p.strip() for p in parts if p.strip())


def build_email_message(note: Notification, sender: str, recipients: Sequence[str]) -> EmailMessage:
    """Render a Notification (subject/body + attachments) into an EmailMessage."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = note.subject
    msg.set_content(note.body or "")
    for path in note.attachments:
        path = Path(path)
        data = path.read_bytes()
        guessed, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg


class EmailChannel:
    """Sends a Notification (with attachments) over SMTP — Gmail SSL by default.

    SMTP is a different protocol from the crawler's HTTP path, so it does not go
    through ``PoliteHttpClient``; robots/rate-limit don't apply to sending your own
    mail. ``dry_run_dir`` writes the composed ``.eml`` there instead of sending, so
    the full pipeline can be exercised without touching the network or credentials.
    """

    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: Sequence[str],
        use_ssl: bool = True,
        dry_run_dir: Path | None = None,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender or user
        self.recipients = tuple(recipients)
        self.use_ssl = use_ssl
        self.dry_run_dir = Path(dry_run_dir) if dry_run_dir else None
        self._smtp_factory = smtp_factory

    def send(self, note: Notification) -> None:
        if not self.recipients:
            raise ValueError("EmailChannel has no recipients")
        msg = build_email_message(note, self.sender, self.recipients)

        if self.dry_run_dir is not None:
            self.dry_run_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out = self.dry_run_dir / f"email-{stamp}.eml"
            out.write_bytes(bytes(msg))
            return

        if self._smtp_factory is not None:
            client = self._smtp_factory(self.host, self.port)
        elif self.use_ssl:
            client = smtplib.SMTP_SSL(self.host, self.port)
        else:
            client = smtplib.SMTP(self.host, self.port)
        with client as server:
            if not self.use_ssl and self._smtp_factory is None:
                server.starttls()
            if self.user and self.password:
                server.login(self.user, self.password)
            server.send_message(msg)


def dispatch(channels: Sequence[NotificationChannel], note: Notification) -> list[str]:
    """Send to every channel; return names that failed (one channel must not block others)."""
    failed: list[str] = []
    for channel in channels:
        try:
            channel.send(note)
        except Exception:
            failed.append(channel.name)
    return failed


def build_summary(rows, latest_excel: Path | None = None, failure: str | None = None) -> Notification:
    if failure:
        return Notification(
            subject="ProjectIntel 运行失败",
            body=f"原因：{failure}\n（已保留上一次 Excel）",
        )

    summary = build_report(rows)
    lines = [
        f"线索总数：{summary.total}（有联系方式 {summary.with_contact}，覆盖率 {summary.coverage:.0%}）",
        f"本次新增：{summary.new_today}",
        f"高价值线索：{summary.high_value}",
        f"RFP（路径A）：{summary.rfp_leads}",
        f"待补全（无联系方式）：{summary.pending}",
    ]
    if latest_excel:
        lines.append(f"最新 Excel：{latest_excel.as_posix()}")
    return Notification(subject="ProjectIntel 数据更新", body="\n".join(lines))
