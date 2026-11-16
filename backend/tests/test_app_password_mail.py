"""Gmail through IMAP/SMTP with an app password, against a simulated Gmail server."""

import imaplib
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from app.connectors import mail as m
from app.models import MailAccount
from app.security import encrypt_secret

PASSWORD = "abcdefghijklmnop"


class FakeGmail:
    """Just enough of Gmail's IMAP (with X-GM extensions) and SMTP for the provider."""

    def __init__(self):
        self.messages: list[dict] = []   # uid, msgid, thrid, raw
        self.validity = 7

    def add(self, raw: bytes, thrid: int | None = None) -> dict:
        uid = len(self.messages) + 100
        msg = {"uid": uid, "msgid": 1_000_000 + uid, "thrid": thrid or 1_000_000 + uid, "raw": raw}
        self.messages.append(msg)
        return msg

    def imap(self, host, timeout=None):
        gmail = self

        class Conn:
            def login(self, user, pw):
                if pw != PASSWORD:
                    raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

            def list(self):
                return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/Alle Nachrichten"']

            def select(self, name, readonly=False):
                assert name == '"[Gmail]/Alle Nachrichten"'
                return "OK", [b"1"]

            def status(self, name, what):
                nxt = (gmail.messages[-1]["uid"] + 1) if gmail.messages else 100
                return "OK", [f"{name} (UIDVALIDITY {gmail.validity} UIDNEXT {nxt})".encode()]

            def uid(self, cmd, *args):
                if cmd == "SEARCH":
                    kind, *rest = args
                    if kind == "X-GM-THRID":
                        hits = [x for x in gmail.messages if str(x["thrid"]) == rest[0]]
                    elif kind == "X-GM-MSGID":
                        hits = [x for x in gmail.messages if str(x["msgid"]) == rest[0]]
                    elif kind == "HEADER":
                        hits = [x for x in gmail.messages if rest[1].strip('"').encode() in x["raw"]]
                    else:  # X-GM-RAW: from:<address>
                        senders = [s.split(":")[1] for s in rest[0].strip('"(').split(")")[0].split(" OR ")]
                        hits = [x for x in gmail.messages if any(s in m.email.message_from_bytes(x["raw"])["From"] for s in senders)]
                    return "OK", [b" ".join(str(x["uid"]).encode() for x in hits)]
                ids, what = args
                if ":" in ids:
                    start = int(ids.split(":")[0])
                    rows = [x for x in gmail.messages if x["uid"] >= start] or gmail.messages[-1:]
                else:
                    wanted = {int(i) for i in ids.split(",")}
                    rows = [x for x in gmail.messages if x["uid"] in wanted]
                if "BODY" in what:
                    out = []
                    for x in rows:
                        out.append((f'1 (X-GM-MSGID {x["msgid"]} X-GM-THRID {x["thrid"]} UID {x["uid"]} BODY[] {{1}}'.encode(), x["raw"]))
                        out.append(b")")
                    return "OK", out
                return "OK", [f'1 (X-GM-MSGID {x["msgid"]} X-GM-THRID {x["thrid"]} UID {x["uid"]})'.encode() for x in rows]

            def logout(self):
                pass

        return Conn()

    def smtp(self, host, port, timeout=None):
        gmail = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, user, pw):
                if pw != PASSWORD:
                    raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

            def send_message(self, msg):
                gmail.add(msg.as_bytes())   # Gmail files the sent copy in All Mail

        return Conn()


def _raw(sender, to, subject, body, in_reply_to=None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg["Date"] = format_datetime(datetime.now(UTC) + timedelta(minutes=1))  # always just after anything sent now
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@mail.example>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg.as_bytes()


@pytest.fixture
def gmail(monkeypatch):
    fake = FakeGmail()
    monkeypatch.setattr(m.imaplib, "IMAP4_SSL", fake.imap)
    monkeypatch.setattr(m.smtplib, "SMTP_SSL", fake.smtp)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    return fake


def _account(password=PASSWORD) -> MailAccount:
    return MailAccount(id=uuid.uuid4(), user_id=uuid.uuid4(), provider="gmail_app", address="alex.jobs@gmail.com",
                       encrypted_refresh_token=encrypt_secret(password))


def test_app_password_format_is_forgiving():
    assert m.normalize_app_password(" abcd EFGH ijkl mnop ") == PASSWORD


def test_checking_the_app_password(gmail):
    m.check_app_password("alex.jobs@gmail.com", PASSWORD)
    with pytest.raises(m.MailAuthError):
        m.check_app_password("alex.jobs@gmail.com", "wrongwrongwrongw")


def test_send_read_and_reply_in_the_same_conversation(gmail):
    provider, account = m.GmailAppPasswordProvider(), _account()
    refs, position = provider.list_new(account)
    assert refs == [] and position == "7:100"           # the first check only marks where to start
    account.last_history_id = position

    sent = provider.send(account, m.OutgoingMail(to="jobs@acme.at", subject="Backend Engineer", body="Hello", from_name="Alex"))
    assert sent.thread_id == format(gmail.messages[0]["thrid"], "x") and sent.rfc_message_id

    reply = gmail.add(_raw("Jobs <jobs@acme.at>", "alex.jobs@gmail.com", "Re: Backend Engineer", "Thanks, when can you talk?",
                           sent.rfc_message_id), thrid=gmail.messages[0]["thrid"])
    gmail.add(_raw("news@shop.example", "alex.jobs@gmail.com", "Sale", "50% off"))
    refs, position = provider.list_new(account)
    assert [r.thread_id for r in refs].count(sent.thread_id) == 2 and len(refs) == 3   # our copy, the reply, the newsletter
    assert all(not r.sender for r in refs)                                              # bare references only
    account.last_history_id = position
    assert provider.list_new(account)[0] == []

    msg = provider.get_message(account, format(reply["msgid"], "x"))
    assert msg.sender == "jobs@acme.at" and "when can you talk" in msg.body and msg.received_at.year == 2026
    thread = provider.get_thread(account, sent.thread_id)
    assert [t.sender for t in thread] == ["alex.jobs@gmail.com", "jobs@acme.at"]

    found = provider.search_from(account, ["jobs@acme.at"], datetime.now(UTC) - timedelta(days=1))
    assert [f.subject for f in found] == ["Re: Backend Engineer"]


def test_a_revoked_app_password_asks_the_user_to_reconnect(gmail):
    provider, account = m.GmailAppPasswordProvider(), _account(password="revokedrevokedre")
    with pytest.raises(m.MailAuthError):
        provider.list_new(account)
    with pytest.raises(m.MailAuthError):
        provider.send(account, m.OutgoingMail(to="jobs@acme.at", subject="x", body="y"))


def test_each_account_uses_its_own_kind_of_connection():
    router = m.RoutingMailProvider()
    assert router._for(_account()) is router.app_password
    assert router._for(MailAccount(provider="gmail")) is router.oauth


def test_connecting_with_an_app_password(client, monkeypatch):
    from tests.conftest import make_user

    headers, _ = make_user(client)
    bad = client.post("/api/mail/app-password", json={"address": "alex.jobs@gmail.com", "app_password": "my real password"},
                      headers=headers)
    assert bad.status_code == 400 and "16 letters" in bad.json()["detail"]
    ok = client.post("/api/mail/app-password", json={"address": "Alex.Jobs@gmail.com", "app_password": "abcd efgh ijkl mnop"},
                     headers=headers)
    assert ok.status_code == 200 and ok.json()["address"] == "alex.jobs@gmail.com"
    rows = client.get("/api/mail/account", headers=headers).json()
    assert rows[0]["provider"] == "gmail_app" and rows[0]["status"] == "connected"
    assert PASSWORD not in str(rows)
