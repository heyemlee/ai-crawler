from email import message_from_bytes, policy
from pathlib import Path

import pytest

from bay_area_projectintel.notify import (
    EmailChannel,
    Notification,
    build_email_message,
    parse_recipients,
)


def test_parse_recipients_handles_separators_and_blanks() -> None:
    assert parse_recipients("a@x.com, b@y.com") == ("a@x.com", "b@y.com")
    assert parse_recipients("a@x.com; b@y.com\nc@z.com") == ("a@x.com", "b@y.com", "c@z.com")
    assert parse_recipients("  a@x.com ,, ") == ("a@x.com",)
    assert parse_recipients(None) == ()
    assert parse_recipients("") == ()


def test_build_email_message_sets_headers_and_attaches(tmp_path: Path) -> None:
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(b"PK\x03\x04 fake xlsx bytes")
    note = Notification("Subj", "Body line", attachments=(xlsx,))

    msg = build_email_message(note, "me@gmail.com", ["a@x.com", "b@y.com"])

    assert msg["From"] == "me@gmail.com"
    assert msg["To"] == "a@x.com, b@y.com"
    assert msg["Subject"] == "Subj"
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    part = attachments[0]
    assert part.get_filename() == "leads.xlsx"
    # .xlsx maps to the Office Open XML spreadsheet MIME type.
    assert part.get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert part.get_payload(decode=True) == b"PK\x03\x04 fake xlsx bytes"


def test_dry_run_writes_eml_and_does_not_send(tmp_path: Path) -> None:
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(b"data")
    out_dir = tmp_path / "out"
    channel = EmailChannel(
        host="smtp.gmail.com",
        port=465,
        user="me@gmail.com",
        password="app-pass",
        sender="me@gmail.com",
        recipients=["a@x.com"],
        dry_run_dir=out_dir,
    )
    channel.send(Notification("Subj", "Body", attachments=(xlsx,)))

    eml = list(out_dir.glob("*.eml"))
    assert len(eml) == 1
    parsed = message_from_bytes(eml[0].read_bytes(), policy=policy.default)
    assert parsed["To"] == "a@x.com"
    assert [p.get_filename() for p in parsed.iter_attachments()] == ["leads.xlsx"]


def test_send_uses_injected_smtp_and_logs_in() -> None:
    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port):
            sent["addr"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["msg_to"] = msg["To"]

    channel = EmailChannel(
        host="smtp.gmail.com",
        port=465,
        user="me@gmail.com",
        password="app-pass",
        sender="me@gmail.com",
        recipients=["a@x.com"],
        smtp_factory=FakeSMTP,
    )
    channel.send(Notification("S", "B"))

    assert sent["addr"] == ("smtp.gmail.com", 465)
    assert sent["login"] == ("me@gmail.com", "app-pass")
    assert sent["msg_to"] == "a@x.com"


def test_send_without_recipients_raises() -> None:
    channel = EmailChannel(
        host="h", port=465, user="u", password="p", sender="u", recipients=[]
    )
    with pytest.raises(ValueError):
        channel.send(Notification("S", "B"))
