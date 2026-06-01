from pathlib import Path

from bay_area_projectintel.notify import (
    FileChannel,
    Notification,
    StdoutChannel,
    build_summary,
    dispatch,
)


def _rows():
    return [
        {"email": "a@x.com", "phone": None, "category": "PUBLIC_WORKS", "source": "samgov", "first_seen": "2026-05-28"},
        {"email": None, "phone": "(415) 555-1212", "category": "OTHER", "source": "datasf", "first_seen": "2026-05-28"},
        {"email": None, "phone": None, "category": "OTHER", "source": "marin", "first_seen": "2026-05-01"},
    ]


def test_stdout_channel_prints_subject_and_body() -> None:
    captured: list[str] = []
    StdoutChannel(printer=captured.append).send(Notification("Subj", "Body line"))
    assert captured == ["Subj\nBody line"]


def test_file_channel_appends(tmp_path: Path) -> None:
    log = tmp_path / "notify.log"
    channel = FileChannel(log)
    channel.send(Notification("First", "one"))
    channel.send(Notification("Second", "two"))
    text = log.read_text(encoding="utf-8")
    assert "First" in text and "Second" in text
    assert text.count("one") == 1


def test_dispatch_isolates_failing_channel() -> None:
    class Boom:
        name = "boom"

        def send(self, note):
            raise RuntimeError("nope")

    sent: list[str] = []

    class Ok:
        name = "ok"

        def send(self, note):
            sent.append(note.subject)

    failed = dispatch([Boom(), Ok()], Notification("S", "B"))
    assert failed == ["boom"]
    assert sent == ["S"]


def test_build_summary_reports_counts_and_excel_pointer() -> None:
    note = build_summary(_rows(), latest_excel=Path("data/latest-leads.xlsx"))
    assert "线索总数：3" in note.body
    assert "待补全（无联系方式）：1" in note.body
    assert "data/latest-leads.xlsx" in note.body


def test_build_summary_failure_mode() -> None:
    note = build_summary([], failure="network down")
    assert "失败" in note.subject
    assert "network down" in note.body
