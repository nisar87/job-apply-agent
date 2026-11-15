"""Mail behind one interface (master document, section 6).

`GmailProvider` talks to the Gmail REST API with the two scopes `gmail.send` and
`gmail.readonly`. `GmailAppPasswordProvider` uses Gmail's IMAP and SMTP servers with an app password, which
needs no Google app approval. `FakeMailProvider` is an in-memory mailbox for tests and dry runs.
"""

import base64
import email
import imaplib
import re
import smtplib
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy as email_policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from typing import Protocol

import httpx

from app.config import get_settings
from app.models import MailAccount
from app.security import decrypt_secret

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


@dataclass
class Attachment:
    name: str
    data: bytes
    mime: str = "application/pdf"


@dataclass
class OutgoingMail:
    to: str
    subject: str
    body: str
    from_name: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    thread_id: str | None = None
    in_reply_to: str | None = None  # RFC Message-ID header of the message being answered


@dataclass
class SentMail:
    message_id: str
    thread_id: str
    rfc_message_id: str = ""


@dataclass
class MailMessage:
    message_id: str
    thread_id: str
    sender: str
    to: str
    subject: str
    body: str
    received_at: datetime
    rfc_message_id: str = ""
    is_bounce: bool = False


class MailAuthError(Exception):
    """The stored authorization no longer works; the user must reconnect."""


class MailProvider(Protocol):
    def send(self, account: MailAccount, mail: OutgoingMail) -> SentMail: ...
    def get_thread(self, account: MailAccount, thread_id: str) -> list[MailMessage]: ...
    def list_new(self, account: MailAccount) -> tuple[list[MailMessage], str | None]: ...
    def search_from(self, account: MailAccount, senders: list[str], since: datetime) -> list[MailMessage]: ...
    def watch(self, account: MailAccount) -> tuple[datetime | None, str | None]: ...


def build_mime(account_address: str, mail: OutgoingMail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((mail.from_name, account_address)) if mail.from_name else account_address
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Message-ID"] = make_msgid()
    if mail.in_reply_to:
        msg["In-Reply-To"] = mail.in_reply_to
        msg["References"] = mail.in_reply_to
    msg.set_content(mail.body)
    for att in mail.attachments:
        maintype, _, subtype = att.mime.partition("/")
        msg.add_attachment(att.data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=att.name)
    return msg


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _plain_body(payload: dict) -> str:
    """Best plain-text body of a Gmail message payload."""
    if payload.get("mimeType", "").startswith("text/plain") and payload.get("body", {}).get("data"):
        return _b64url_decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    if payload.get("mimeType", "").startswith("text/html") and payload.get("body", {}).get("data"):
        from app.connectors.fetcher import html_to_text

        return html_to_text(_b64url_decode(payload["body"]["data"]).decode("utf-8", errors="replace"))
    return ""


class GmailProvider:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)
        self._tokens: dict[uuid.UUID, tuple[str, datetime]] = {}

    def _access_token(self, account: MailAccount) -> str:
        cached = self._tokens.get(account.id)
        if cached and cached[1] > datetime.now(UTC):
            return cached[0]
        s = get_settings()
        resp = self.http.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "refresh_token": decrypt_secret(account.encrypted_refresh_token),
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code in (400, 401):
            raise MailAuthError(resp.text[:200])
        resp.raise_for_status()
        data = resp.json()
        expires = datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
        self._tokens[account.id] = (data["access_token"], expires)
        return data["access_token"]

    def _request(self, account: MailAccount, method: str, path: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token(account)}"}
        resp = self.http.request(method, f"{GMAIL_API}{path}", headers=headers, **kw)
        if resp.status_code == 401:
            self._tokens.pop(account.id, None)
            raise MailAuthError("Gmail rejected the access token")
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _to_message(self, raw: dict) -> MailMessage:
        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        sender = parseaddr(headers.get("from", ""))[1].lower()
        subject = headers.get("subject", "")
        is_bounce = sender.startswith(("mailer-daemon@", "postmaster@")) or "delivery status notification" in subject.lower()
        return MailMessage(
            message_id=raw["id"], thread_id=raw.get("threadId", ""), sender=sender,
            to=parseaddr(headers.get("to", ""))[1].lower(), subject=subject, body=_plain_body(raw.get("payload", {})),
            received_at=datetime.fromtimestamp(int(raw.get("internalDate", "0")) / 1000, UTC),
            rfc_message_id=headers.get("message-id", ""), is_bounce=is_bounce,
        )

    def send(self, account: MailAccount, mail: OutgoingMail) -> SentMail:
        mime = build_mime(account.address, mail)
        body: dict = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
        if mail.thread_id:
            body["threadId"] = mail.thread_id
        data = self._request(account, "POST", "/messages/send", json=body)
        return SentMail(message_id=data["id"], thread_id=data.get("threadId", ""), rfc_message_id=mime["Message-ID"])

    def get_thread(self, account: MailAccount, thread_id: str) -> list[MailMessage]:
        data = self._request(account, "GET", f"/threads/{thread_id}", params={"format": "full"})
        return [self._to_message(m) for m in data.get("messages", [])]

    def list_new(self, account: MailAccount) -> tuple[list[MailMessage], str | None]:
        """Messages added since the stored history ID. Returns only references that the
        caller then filters to tracked threads before reading anything in full."""
        if not account.last_history_id:
            profile = self._request(account, "GET", "/profile")
            return [], str(profile.get("historyId"))
        messages: list[MailMessage] = []
        page, latest = None, account.last_history_id
        while True:
            params = {"startHistoryId": account.last_history_id, "historyTypes": "messageAdded"}
            if page:
                params["pageToken"] = page
            data = self._request(account, "GET", "/history", params=params)
            latest = str(data.get("historyId", latest))
            for h in data.get("history", []):
                for added in h.get("messagesAdded", []):
                    ref = added["message"]
                    messages.append(
                        MailMessage(message_id=ref["id"], thread_id=ref.get("threadId", ""), sender="", to="",
                                    subject="", body="", received_at=datetime.now(UTC))
                    )
            page = data.get("nextPageToken")
            if not page:
                break
        return messages, latest

    def get_message(self, account: MailAccount, message_id: str) -> MailMessage:
        return self._to_message(self._request(account, "GET", f"/messages/{message_id}", params={"format": "full"}))

    def search_from(self, account: MailAccount, senders: list[str], since: datetime) -> list[MailMessage]:
        if not senders:
            return []
        query = "(" + " OR ".join(f"from:{s}" for s in senders[:40]) + f") after:{int(since.timestamp())}"
        data = self._request(account, "GET", "/messages", params={"q": query, "maxResults": 50})
        return [self.get_message(account, m["id"]) for m in data.get("messages", [])]

    def watch(self, account: MailAccount) -> tuple[datetime | None, str | None]:
        topic = get_settings().google_pubsub_topic
        if not topic:
            return None, None
        data = self._request(account, "POST", "/watch", json={"topicName": topic, "labelIds": ["INBOX"]})
        expiry = datetime.fromtimestamp(int(data["expiration"]) / 1000, UTC)
        return expiry, str(data.get("historyId"))


IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
_FETCH_META = re.compile(rb"X-GM-MSGID (\d+)|X-GM-THRID (\d+)|UID (\d+)")
_LIST_LINE = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def normalize_app_password(value: str) -> str:
    """Google shows app passwords as four groups of four letters; spaces are optional."""
    return re.sub(r"\s+", "", value or "").lower()


def _hex(dec: bytes | str) -> str:
    return format(int(dec), "x")


def _imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_message(raw: bytes, message_id: str, thread_id: str) -> MailMessage:
    msg = email.message_from_bytes(raw, policy=email_policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    subject = str(msg.get("Subject", ""))
    part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            from app.connectors.fetcher import html_to_text

            body = html_to_text(body)
    try:
        received = parsedate_to_datetime(str(msg["Date"])).astimezone(UTC)
    except (TypeError, ValueError, IndexError):
        received = datetime.now(UTC)
    is_bounce = sender.startswith(("mailer-daemon@", "postmaster@")) or "delivery status notification" in subject.lower()
    return MailMessage(message_id=message_id, thread_id=thread_id, sender=sender,
                       to=parseaddr(str(msg.get("To", "")))[1].lower(), subject=subject, body=body, received_at=received,
                       rfc_message_id=str(msg.get("Message-ID", "")), is_bounce=is_bounce)


def check_app_password(address: str, password: str) -> None:
    """Sign in to the mailbox once; raises MailAuthError when Google refuses the address and app password."""
    conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
    try:
        conn.login(address, password)
    except imaplib.IMAP4.error as exc:
        raise MailAuthError("Google did not accept this address and app password") from exc
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


class GmailAppPasswordProvider:
    """Gmail through its own IMAP and SMTP servers, signed in with an app password (a 16-letter code from the
    user's Google account, not their real password). It needs no Google app approval and no test-user list.
    Message and thread ids are Gmail's own (X-GM-MSGID / X-GM-THRID, in hex like the Gmail API), so the rest
    of the agent treats both kinds of account the same. Read position: "<uidvalidity>:<next uid>"."""

    def _password(self, account: MailAccount) -> str:
        return decrypt_secret(account.encrypted_refresh_token)

    @contextmanager
    def _imap(self, account: MailAccount):
        conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
        try:
            try:
                conn.login(account.address, self._password(account))
            except imaplib.IMAP4.error as exc:
                raise MailAuthError("Gmail no longer accepts the app password") from exc
            conn.select(self._all_mail(conn), readonly=True)
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _all_mail(conn) -> str:
        """The "All Mail" folder, found by its role because its name depends on the account's language."""
        _, lines = conn.list()
        for line in lines or []:
            text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            m = _LIST_LINE.match(text)
            if m and "\\All" in m.group("flags"):
                return m.group("name")
        return '"[Gmail]/All Mail"'

    @staticmethod
    def _search(conn, *criteria: str) -> list[bytes]:
        typ, data = conn.uid("SEARCH", *criteria)
        return data[0].split() if typ == "OK" and data and data[0] else []

    @staticmethod
    def _fetch_full(conn, uids: list[bytes]) -> list[MailMessage]:
        if not uids:
            return []
        typ, data = conn.uid("FETCH", b",".join(uids).decode(), "(X-GM-MSGID X-GM-THRID BODY.PEEK[])")
        out = []
        for item in data or []:
            if not isinstance(item, tuple):
                continue
            meta = {"msgid": None, "thrid": None, "uid": None}
            for msgid, thrid, uid in _FETCH_META.findall(item[0]):
                meta["msgid"] = msgid or meta["msgid"]
                meta["thrid"] = thrid or meta["thrid"]
                meta["uid"] = uid or meta["uid"]
            if meta["msgid"]:
                out.append(_parse_message(item[1], _hex(meta["msgid"]), _hex(meta["thrid"] or meta["msgid"])))
        return sorted(out, key=lambda m: m.received_at)

    def send(self, account: MailAccount, mail: OutgoingMail) -> SentMail:
        mime = build_mime(account.address, mail)
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=60) as smtp:
                smtp.login(account.address, self._password(account))
                smtp.send_message(mime)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailAuthError("Gmail no longer accepts the app password") from exc
        rfc = mime["Message-ID"]
        # Gmail files the sent copy within a moment; its ids keep replies in the same conversation.
        for wait in (1, 2, 3, 4):
            time.sleep(wait)
            try:
                with self._imap(account) as conn:
                    found = self._fetch_full(conn, self._search(conn, "HEADER", "Message-ID", _imap_quote(rfc)))
                if found:
                    return SentMail(message_id=found[0].message_id, thread_id=found[0].thread_id, rfc_message_id=rfc)
            except MailAuthError:
                raise
            except Exception:  # noqa: BLE001 - the mail is out; only the lookup failed
                continue
        return SentMail(message_id=rfc.strip("<>"), thread_id=mail.thread_id or "m:" + rfc.strip("<>"), rfc_message_id=rfc)

    def get_thread(self, account: MailAccount, thread_id: str) -> list[MailMessage]:
        if thread_id.startswith("m:"):
            return []
        with self._imap(account) as conn:
            return self._fetch_full(conn, self._search(conn, "X-GM-THRID", str(int(thread_id, 16))))

    def get_message(self, account: MailAccount, message_id: str) -> MailMessage:
        with self._imap(account) as conn:
            found = self._fetch_full(conn, self._search(conn, "X-GM-MSGID", str(int(message_id, 16))))
        if not found:
            raise KeyError(message_id)
        return found[0]

    def list_new(self, account: MailAccount) -> tuple[list[MailMessage], str | None]:
        with self._imap(account) as conn:
            _, status = conn.status(self._all_mail(conn), "(UIDVALIDITY UIDNEXT)")
            text = status[0].decode() if status and status[0] else ""
            validity = (re.search(r"UIDVALIDITY (\d+)", text) or [None, "0"])[1]
            uidnext = int((re.search(r"UIDNEXT (\d+)", text) or [None, "1"])[1])
            position = f"{validity}:{uidnext}"
            saved_validity, _, saved_next = (account.last_history_id or "").partition(":")
            if not saved_next or saved_validity != validity:     # first run, or Gmail renumbered the folder
                return [], position
            start = int(saved_next)
            if start >= uidnext:
                return [], position
            typ, data = conn.uid("FETCH", f"{start}:*", "(X-GM-MSGID X-GM-THRID)")
        refs = []
        for item in data or []:
            line = item[0] if isinstance(item, tuple) else item
            if not isinstance(line, bytes):
                continue
            meta = {"msgid": None, "thrid": None, "uid": None}
            for msgid, thrid, uid in _FETCH_META.findall(line):
                meta["msgid"] = msgid or meta["msgid"]
                meta["thrid"] = thrid or meta["thrid"]
                meta["uid"] = uid or meta["uid"]
            if meta["msgid"] and meta["uid"] and int(meta["uid"]) >= start:
                refs.append(MailMessage(message_id=_hex(meta["msgid"]), thread_id=_hex(meta["thrid"] or meta["msgid"]),
                                        sender="", to="", subject="", body="", received_at=datetime.now(UTC)))
        return refs, position

    def search_from(self, account: MailAccount, senders: list[str], since: datetime) -> list[MailMessage]:
        if not senders:
            return []
        query = "(" + " OR ".join(f"from:{s}" for s in senders[:40]) + f") after:{int(since.timestamp())}"
        with self._imap(account) as conn:
            return self._fetch_full(conn, self._search(conn, "X-GM-RAW", _imap_quote(query))[-50:])

    def watch(self, account: MailAccount) -> tuple[datetime | None, str | None]:
        return None, None     # no push for IMAP; the regular inbox check reads new mail


class RoutingMailProvider:
    """Sends each call to the provider matching the account: Google sign-in ("gmail") or app password ("gmail_app")."""

    def __init__(self):
        self.oauth = GmailProvider()
        self.app_password = GmailAppPasswordProvider()

    def _for(self, account: MailAccount):
        return self.app_password if account.provider == "gmail_app" else self.oauth

    def send(self, account, mail):
        return self._for(account).send(account, mail)

    def get_thread(self, account, thread_id):
        return self._for(account).get_thread(account, thread_id)

    def get_message(self, account, message_id):
        return self._for(account).get_message(account, message_id)

    def list_new(self, account):
        return self._for(account).list_new(account)

    def search_from(self, account, senders, since):
        return self._for(account).search_from(account, senders, since)

    def watch(self, account):
        return self._for(account).watch(account)


class FakeMailProvider:
    """In-memory mailbox. Tests call `deliver` to simulate an employer's reply."""

    def __init__(self):
        self.sent: list[tuple[str, OutgoingMail, SentMail]] = []
        self.threads: dict[str, list[MailMessage]] = {}
        self.pending: dict[str, list[MailMessage]] = {}  # account address -> unread
        self.fail_next: Exception | None = None
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def send(self, account: MailAccount, mail: OutgoingMail) -> SentMail:
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        thread_id = mail.thread_id or self._id("t")
        sent = SentMail(message_id=self._id("m"), thread_id=thread_id, rfc_message_id=f"<{uuid.uuid4().hex}@fake>")
        self.sent.append((account.address, mail, sent))
        self.threads.setdefault(thread_id, []).append(
            MailMessage(message_id=sent.message_id, thread_id=thread_id, sender=account.address.lower(), to=mail.to.lower(),
                        subject=mail.subject, body=mail.body, received_at=datetime.now(UTC), rfc_message_id=sent.rfc_message_id)
        )
        return sent

    def deliver(self, account_address: str, *, sender: str, body: str, subject: str = "Re:", thread_id: str | None = None,
                is_bounce: bool = False, when: datetime | None = None) -> MailMessage:
        thread_id = thread_id or self._id("t")
        msg = MailMessage(message_id=self._id("m"), thread_id=thread_id, sender=sender.lower(), to=account_address.lower(),
                          subject=subject, body=body, received_at=when or datetime.now(UTC),
                          rfc_message_id=f"<{uuid.uuid4().hex}@fake>", is_bounce=is_bounce)
        self.threads.setdefault(thread_id, []).append(msg)
        self.pending.setdefault(account_address.lower(), []).append(msg)
        return msg

    def get_thread(self, account: MailAccount, thread_id: str) -> list[MailMessage]:
        return list(self.threads.get(thread_id, []))

    def get_message(self, account: MailAccount, message_id: str) -> MailMessage:
        for msgs in self.threads.values():
            for m in msgs:
                if m.message_id == message_id:
                    return m
        raise KeyError(message_id)

    def list_new(self, account: MailAccount) -> tuple[list[MailMessage], str | None]:
        msgs = self.pending.pop(account.address.lower(), [])
        return msgs, str(self._n)

    def search_from(self, account: MailAccount, senders: list[str], since: datetime) -> list[MailMessage]:
        out = []
        for msgs in self.threads.values():
            for m in msgs:
                if m.to == account.address.lower() and m.received_at >= since and any(
                    m.sender == s or m.sender.endswith("@" + s) for s in senders
                ):
                    out.append(m)
        return out

    def watch(self, account: MailAccount) -> tuple[datetime | None, str | None]:
        return datetime.now(UTC) + timedelta(days=7), str(self._n)


_provider: MailProvider | None = None


def set_mail_provider(provider: MailProvider | None) -> None:
    global _provider
    _provider = provider


def get_mail_provider() -> MailProvider:
    global _provider
    if _provider is None:
        _provider = FakeMailProvider() if get_settings().mail_provider == "fake" else RoutingMailProvider()
    return _provider
