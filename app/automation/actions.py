# -*- coding: utf-8 -*-
"""
Automation Action Handler (Issue #22).

Applies structured actions returned by the rule engine to the database using
a caller-supplied SQLAlchemy session.  No Flask app context is required and
no internal commits are made; all writes are deferred to the caller's
transaction boundary.

Usage::

    from app.automation.actions import apply_actions

    result = evaluate_request(now, booking)
    apply_actions(db_session, booking, result["actions"])
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, Notification, User

_logger = logging.getLogger(__name__)

# Lazy import guard: AccessRequestStatusHistory may not exist in all
# deployments; we import it only when needed rather than at module level.
try:
    from ..models import AccessRequest
    from ..models import AccessRequestStatusHistory as _ARSH
    from ..models import BookingRequest as _BR
    _ARSH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ARSH_AVAILABLE = False
    AccessRequest = None  # type: ignore[assignment,misc]
    _ARSH = None  # type: ignore[assignment]
    _BR = None  # type: ignore[assignment]

_SYSTEM_ACTOR = "system@scheduler"
_DEFAULT_RULE_VERSION = "automation_rules_v1.1"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_actions(
    db_session: Session,
    request: Any,
    actions: List[dict],
    *,
    now: Optional[datetime] = None,
    rule_version: str = _DEFAULT_RULE_VERSION,
) -> None:
    """Apply rule-engine *actions* to the database within *db_session*.

    Parameters
    ----------
    db_session:
        SQLAlchemy ``Session`` used for all writes.  The caller is responsible
        for committing or rolling back.
    request:
        An ``AccessRequest`` or ``BookingRequest`` ORM instance.
    actions:
        List of action dicts from ``evaluate_request`` (keys: ``type``,
        ``reason``, and optionally ``new_status`` / ``audience``).
    now:
        UTC timestamp injected for testability.  Defaults to
        ``datetime.utcnow()`` when omitted.
    rule_version:
        Rule-set version string recorded in every audit entry.
    """
    if now is None:
        now = datetime.utcnow()

    entity_type = type(request).__name__
    entity_id = request.id

    for action in actions:
        action_type = action.get("type")
        reason = action.get("reason", "")

        if action_type == "STATUS_CHANGE":
            # Audit action is type-prefixed so STATUS_CHANGE and NOTIFY audits
            # for the same reason code never collide in the idempotency check.
            audit_action = f"automation:STATUS_CHANGE:{reason}"
            _handle_status_change(
                db_session, request, action, entity_type, entity_id,
                audit_action, reason, rule_version, now,
            )

        elif action_type == "NOTIFY":
            # Per docs/automation_rules.md §10.3: automation:<reason_code>
            audit_action = f"automation:{reason}"
            _handle_notify(
                db_session, request, action, entity_type, entity_id,
                audit_action, reason, rule_version, now,
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _audit_exists(
    db_session: Session,
    audit_action: str,
    entity_id: Any,
) -> bool:
    """Return True if an audit entry for this action/entity already exists.

    The detail format is:
        ``rule_version=... entity_type=... entity_id=<id> reason=...``
    entity_id is always followed by a space, so the trailing-space pattern
    ``entity_id=<id> `` avoids false prefix matches (e.g. id=1 vs id=12).
    The ``ends with`` clause guards against the unlikely case where entity_id
    becomes the final token.
    """
    prefix_pat = f"%entity_id={entity_id} %"
    suffix_pat = f"%entity_id={entity_id}"
    from sqlalchemy import or_
    row = db_session.execute(
        select(AuditLog).where(
            AuditLog.actor_email == _SYSTEM_ACTOR,
            AuditLog.action == audit_action,
            or_(
                AuditLog.detail.like(prefix_pat),
                AuditLog.detail.like(suffix_pat),
            ),
        )
    ).scalars().first()
    return row is not None


def _handle_status_change(
    db_session: Session,
    request: Any,
    action: dict,
    entity_type: str,
    entity_id: Any,
    audit_action: str,
    reason: str,
    rule_version: str,
    now: datetime,
) -> None:
    new_status = action.get("new_status")
    if not new_status:
        return

    # Natural idempotency: skip if status is already the target value.
    if request.status == new_status:
        _logger.debug(
            "actor=%s action=STATUS_CHANGE entity_type=%s entity_id=%s "
            "new_status=%s skipped=idempotency",
            _SYSTEM_ACTOR, entity_type, entity_id, new_status,
        )
        return

    previous_status = request.status
    request.status = new_status

    # For AccessRequest, also record a status-history entry.
    if _ARSH_AVAILABLE and isinstance(request, AccessRequest):
        db_session.add(_ARSH(
            access_request_id=request.id,
            previous_status=previous_status,
            status=new_status,
            changed_by_id=None,
            changed_at=now,
            note=f"System automation: {reason}",
        ))

    detail = (
        f"rule_version={rule_version} entity_type={entity_type} "
        f"entity_id={entity_id} previous_status={previous_status} "
        f"new_status={new_status} reason={reason}"
    )
    db_session.add(AuditLog(
        at=now,
        actor_email=_SYSTEM_ACTOR,
        action=audit_action,
        detail=detail,
    ))
    _logger.info(
        "actor=%s action=STATUS_CHANGE entity_type=%s entity_id=%s "
        "previous_status=%s new_status=%s reason=%s",
        _SYSTEM_ACTOR, entity_type, entity_id, previous_status, new_status, reason,
    )

    # Cascade expiry to a linked pending AccessRequest when a BookingRequest expires.
    if _BR is not None and isinstance(request, _BR) and new_status == "expired":
        linked_ar = request.access_request
        if linked_ar is not None:
            _cascade_expire_linked_access_request(
                db_session, linked_ar, request.id, now, rule_version
            )


def _handle_notify(
    db_session: Session,
    request: Any,
    action: dict,
    entity_type: str,
    entity_id: Any,
    audit_action: str,
    reason: str,
    rule_version: str,
    now: datetime,
) -> None:
    # Idempotency: skip if we already issued this notification for this entity.
    if _audit_exists(db_session, audit_action, entity_id):
        _logger.debug(
            "actor=%s action=NOTIFY entity_type=%s entity_id=%s reason=%s skipped=idempotency",
            _SYSTEM_ACTOR, entity_type, entity_id, reason,
        )
        return

    audience = action.get("audience", "ADMINS")
    if audience == "ADMINS":
        admins = db_session.execute(
            select(User).where(
                User.role == "admin",
                User.status == "active",
            )
        ).scalars().all()
        message = _notification_message(entity_type, entity_id, reason)
        for admin in admins:
            db_session.add(Notification(
                user_id=admin.id,
                message=message,
                created_at=now,
            ))

    detail = (
        f"rule_version={rule_version} entity_type={entity_type} "
        f"entity_id={entity_id} reason={reason}"
    )
    db_session.add(AuditLog(
        at=now,
        actor_email=_SYSTEM_ACTOR,
        action=audit_action,
        detail=detail,
    ))
    _logger.info(
        "actor=%s action=NOTIFY entity_type=%s entity_id=%s reason=%s audience=%s",
        _SYSTEM_ACTOR, entity_type, entity_id, reason, audience,
    )


def _notification_message(entity_type: str, entity_id: Any, reason: str) -> str:
    if reason == "SLA_WARNING_APPROVAL":
        return f"SLA warning: {entity_type} #{entity_id} is overdue for approval."
    if reason == "SLA_BREACH_APPROVAL":
        return f"SLA breach: {entity_type} #{entity_id} has breached the approval SLA."
    if reason == "AUTO_EXPIRE":
        return f"{entity_type} #{entity_id} has been automatically expired."
    return f"Automation event {reason} for {entity_type} #{entity_id}."


def _cascade_expire_linked_access_request(
    db_session: Session,
    linked_ar: Any,
    booking_id: Any,
    now: datetime,
    rule_version: str,
) -> None:
    """Expire a linked pending AccessRequest when its BookingRequest is auto-expired.

    Mirrors the manual cascade in the admin reject-booking flow: when a
    BookingRequest transitions to ``expired`` via SLA automation, any
    associated ``AccessRequest`` still in ``pending`` state is also expired
    so that it doesn't linger in the approval queue.
    """
    if linked_ar.status != "pending":
        return

    previous_status = linked_ar.status
    linked_ar.status = "expired"
    linked_ar.updated_at = now
    linked_ar.decision_note = (
        f"Auto-expired: linked booking #{booking_id} was expired by SLA automation."
    )

    if _ARSH_AVAILABLE and _ARSH is not None:
        db_session.add(_ARSH(
            access_request_id=linked_ar.id,
            previous_status=previous_status,
            status="expired",
            changed_by_id=None,
            changed_at=now,
            note="System automation: CASCADE_EXPIRE_FROM_BOOKING",
        ))

    cascade_audit_action = "automation:STATUS_CHANGE:CASCADE_EXPIRE_ACCESS_REQUEST"
    detail = (
        f"rule_version={rule_version} entity_type=AccessRequest "
        f"entity_id={linked_ar.id} previous_status={previous_status} "
        f"new_status=expired reason=CASCADE_EXPIRE_FROM_BOOKING "
        f"booking_request_id={booking_id}"
    )
    db_session.add(AuditLog(
        at=now,
        actor_email=_SYSTEM_ACTOR,
        action=cascade_audit_action,
        detail=detail,
    ))
    db_session.add(Notification(
        user_id=linked_ar.requester_id,
        message=(
            f"Access request #{linked_ar.id} has been automatically expired "
            f"because booking #{booking_id} expired."
        ),
        created_at=now,
    ))
    _logger.info(
        "actor=%s action=STATUS_CHANGE entity_type=AccessRequest entity_id=%s "
        "previous_status=%s new_status=expired reason=CASCADE_EXPIRE_FROM_BOOKING "
        "booking_request_id=%s",
        _SYSTEM_ACTOR, linked_ar.id, previous_status, booking_id,
    )
