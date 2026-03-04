# -*- coding: utf-8 -*-
"""
Scheduled background job: Overdue & SLA Monitoring (Issue #24).

Queries pending AccessRequest rows, evaluates SLA rules via the rule engine,
and applies any resulting actions through the action handler.

No Flask app context required.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from ..models import AccessRequest
from .rules import evaluate_request
from .actions import apply_actions

_DEFAULT_RULE_VERSION = "automation_rules_v1.1"


def run_sla_monitoring(
    SessionLocal,
    *,
    now: Optional[datetime] = None,
    rule_version: str = _DEFAULT_RULE_VERSION,
) -> None:
    """Evaluate SLA rules for all pending AccessRequests and apply actions.

    Parameters
    ----------
    SessionLocal:
        A SQLAlchemy ``sessionmaker`` (or ``scoped_session``) factory.
        The job creates and manages its own session.
    now:
        UTC timestamp injected for testability.  Defaults to
        ``datetime.utcnow()`` when omitted.
    rule_version:
        Rule-set version string recorded in every audit entry.
    """
    if now is None:
        now = datetime.utcnow()

    db = SessionLocal()
    try:
        requests = db.execute(
            select(AccessRequest).where(AccessRequest.status == "pending")
        ).scalars().all()

        for request in requests:
            result = evaluate_request(now, request, entity_type="AccessRequest")
            apply_actions(db, request, result["actions"], now=now, rule_version=rule_version)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
