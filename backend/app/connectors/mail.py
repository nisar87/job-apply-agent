"""Mail behind one interface (master document, section 6).

`GmailProvider` talks to the Gmail REST API with the two scopes `gmail.send` and
`gmail.readonly`. `FakeMailProvider` is an in-memory mailbox for tests and dry runs.
"""

import base64
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
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
        _provider = FakeMailProvider() if get_settings().mail_provider == "fake" else GmailProvider()
    return _provider
