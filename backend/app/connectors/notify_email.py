"""The product's own outgoing email: escalations, the weekly digest and account notices
(master document, section 3). It never carries job applications; those leave from the
user's own Gmail."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, to: str, subject: str, body: str) -> bool: ...


class SmtpNotifier:
    def send(self, to: str, subject: str, body: str) -> bool:
        s = get_settings()
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.notify_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return True


class LogNotifier:
    """Used when no SMTP server is configured: notices stay in the app only."""

    def send(self, to: str, subject: str, body: str) -> bool:
        log.info("notification email not sent (no SMTP configured): %s", subject)
        return False


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> bool:
        self.sent.append((to, subject, body))
        return True


_notifier: Notifier | None = None


def set_notifier(n: Notifier | None) -> None:
    global _notifier
    _notifier = n


def get_notifier() -> Notifier:
    if _notifier is not None:
        return _notifier
    return SmtpNotifier() if get_settings().smtp_host else LogNotifier()
