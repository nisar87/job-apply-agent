"""In-app notifications. Email delivery of these is added with the notification email
service (master document, section 3); until then they are stored and shown in the app."""

import uuid

from sqlalchemy.orm import Session

from app.models import Notification


def notify(session: Session, user_id: uuid.UUID, kind: str, title: str, body: str = "", link: str | None = None) -> Notification:
    row = Notification(user_id=user_id, kind=kind, title=title, body=body, link=link)
    session.add(row)
    session.flush()
    return row
