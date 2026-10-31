"""Append-only, hash-chained audit trail. Every write in the workflow goes through `record`."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from app.models import AuditEvent, User

GENESIS = "0" * 64


def _digest(e: AuditEvent) -> str:
    at = e.at if e.at.tzinfo else e.at.replace(tzinfo=UTC)  # SQLite hands back naive datetimes
    body = {"at": at} | {k: getattr(e, k) for k in ("claim_id", "entity", "entity_id", "action", "actor", "actor_role",
                                       "reason", "before", "after", "ai_generated", "model", "prev_hash")}
    raw = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def record(session: Session, *, action: str, entity: str, entity_id: Any = None, claim_id: str | None = None,
           actor: User | str = "system:engine", reason: str | None = None, before: dict | None = None,
           after: dict | None = None, ai_generated: bool = False, model: str | None = None) -> AuditEvent:
    # Chain head is cached per session (one writer per transaction); read from the table on first use.
    prev = session.info.get("audit_head")
    if prev is None:
        last = session.exec(select(AuditEvent).order_by(col(AuditEvent.id).desc())).first()
        prev = last.hash if last else GENESIS
    if isinstance(actor, User):
        name, role = actor.username, actor.role
    else:
        name, role = actor, "SYSTEM"
    e = AuditEvent(at=datetime.now(UTC).replace(microsecond=0), claim_id=claim_id, entity=entity,
                   entity_id=None if entity_id is None else str(entity_id), action=action, actor=name, actor_role=role,
                   reason=reason, before=_jsonable(before), after=_jsonable(after), ai_generated=ai_generated,
                   model=model, prev_hash=prev, hash="")
    e.hash = _digest(e)
    session.add(e)
    session.info["audit_head"] = e.hash
    return e


def _jsonable(d: dict | None) -> dict | None:
    return None if d is None else json.loads(json.dumps(d, default=str))


def verify_chain(session: Session) -> dict:
    prev, n = GENESIS, 0
    for e in session.exec(select(AuditEvent).order_by(col(AuditEvent.id))):
        ok_link = e.prev_hash == prev
        recomputed = _digest(e)
        if not ok_link or recomputed != e.hash:
            return {"valid": False, "events": n, "broken_at": e.id,
                    "reason": "link mismatch" if not ok_link else "content changed after recording"}
        prev, n = e.hash, n + 1
    return {"valid": True, "events": n, "head": prev}
