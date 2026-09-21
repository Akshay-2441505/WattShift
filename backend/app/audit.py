"""Append-only audit trail. This module only ever inserts."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog


def log(session: Session, site_id, now: datetime, actor: str, event: str, ref: str | None = None, **detail) -> None:
    """`detail` values must be JSON-serialisable (pass datetimes as isoformat strings)."""
    session.add(AuditLog(site_id=site_id, at=now, actor=actor, event=event, ref=ref, detail=detail or None))


def log_throttled(
    session: Session, site_id, now: datetime, actor: str, event: str, *, minutes: int = 60, ref: str | None = None, **detail
) -> None:
    """Like log(), but skipped if this site already has the same event in the last `minutes`, so a 30 s poll cannot
    flood the trail with a standing condition (no tariff, mode mismatch)."""
    session.flush()
    recent = session.scalar(
        select(AuditLog.id)
        .where(AuditLog.site_id == site_id, AuditLog.event == event, AuditLog.at > now - timedelta(minutes=minutes))
        .limit(1)
    )
    if recent is None:
        log(session, site_id, now, actor, event, ref, **detail)
