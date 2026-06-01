from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from bay_area_projectintel.report import build_report


@dataclass(frozen=True)
class Notification:
    subject: str
    body: str


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
        lines.append(f"最新 Excel：{latest_excel}")
    return Notification(subject="ProjectIntel 数据更新", body="\n".join(lines))
