"""Append-only audit/history log of meaningful changes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from productpulse.db.models import AuditEvent


def record(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: Any,
    summary: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else "",
        summary=summary,
        details=details or {},
    )
    session.add(event)
    return event


def list_events(session: Session, event_types: list[str] | None = None, limit: int = 500) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    if event_types:
        stmt = stmt.where(AuditEvent.event_type.in_(event_types))
    return list(session.scalars(stmt.limit(limit)))
