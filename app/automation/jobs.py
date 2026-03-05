# -*- coding: utf-8 -*-
"""
Scheduled background jobs: Overdue & SLA Monitoring (Issue #24),
Access Window Monitoring (Issue #25).

Issue #24: Queries pending AccessRequest rows, evaluates SLA rules via the
rule engine, and applies any resulting actions through the action handler.

Issue #25: Monitors booking windows for approved BookingRequests, detecting
starting-soon, active, and missed states, and records audit events.

No Flask app context required.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..models import AccessRequest, AuditLog, BookingRequest, Notification, User
from .rules import evaluate_request
from .actions import apply_actions

_DEFAULT_RULE_VERSION = "automation_rules_v1.1"
_SYSTEM_ACTOR = "system@scheduler"


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


def run_access_window_monitoring(
    SessionLocal,
    *,
    now: Optional[datetime] = None,
    soon_minutes: int = 15,
    rule_version: str = _DEFAULT_RULE_VERSION,
) -> None:
    """Monitor booking windows for approved BookingRequests.

    Detects three states for each booking and records one-time audit events:
    - **Starting soon**: ``start_at - soon_minutes <= now < start_at``
      → audit action ``automation:BOOKING_WINDOW_STARTING_SOON``
      → user notification
    - **Active**: ``start_at <= now <= end_at``
      → no action; booking is in progress.
    - **Missed / no-show**: ``now > end_at`` and not checked-in and not already
      marked no-show → sets ``no_show = True``,
      → audit action ``automation:NO_SHOW_MARKED``
      → user notification

    Both audit events are idempotent: re-running the job will not create
    duplicate audit entries or notifications for the same booking.

    Parameters
    ----------
    SessionLocal:
        A SQLAlchemy ``sessionmaker`` (or ``scoped_session``) factory.
        The job creates and manages its own session.
    now:
        UTC timestamp injected for testability.  Defaults to
        ``datetime.utcnow()`` when omitted.
    soon_minutes:
        How many minutes before ``start_at`` the "starting soon" window opens.
    rule_version:
        Rule-set version string recorded in every audit entry.
    """
    if now is None:
        now = datetime.utcnow()

    horizon = timedelta(hours=24)

    db = SessionLocal()
    try:
        bookings = db.execute(
            select(BookingRequest).where(
                BookingRequest.status == "approved",
                BookingRequest.start_at >= now - horizon,
                BookingRequest.start_at <= now + horizon,
            )
        ).scalars().all()

        for booking in bookings:
            soon_threshold = booking.start_at - timedelta(minutes=soon_minutes)

            if soon_threshold <= now < booking.start_at:
                # Starting-soon window
                _ensure_booking_audit(
                    db,
                    booking=booking,
                    audit_action="automation:BOOKING_WINDOW_STARTING_SOON",
                    message=(
                        f"Your booking #{booking.id} starts in less than "
                        f"{soon_minutes} minutes."
                    ),
                    rule_version=rule_version,
                    now=now,
                )

            elif now > booking.end_at:
                # Window has passed
                if not booking.checked_in and not booking.no_show:
                    booking.no_show = True
                    _ensure_booking_audit(
                        db,
                        booking=booking,
                        audit_action="automation:NO_SHOW_MARKED",
                        message=(
                            f"No-show recorded for booking #{booking.id}. "
                            "If this is incorrect, contact an admin."
                        ),
                        rule_version=rule_version,
                        now=now,
                    )

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _booking_audit_exists(
    db: Session,
    audit_action: str,
    entity_id: int,
) -> bool:
    """Return True if a system audit entry already exists for this booking event."""
    prefix_pat = f"%entity_id={entity_id} %"
    suffix_pat = f"%entity_id={entity_id}"
    row = db.execute(
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


def _ensure_booking_audit(
    db: Session,
    *,
    booking: BookingRequest,
    audit_action: str,
    message: str,
    rule_version: str,
    now: datetime,
) -> None:
    """Write an audit entry and user notification for *booking* if not already present."""
    if _booking_audit_exists(db, audit_action, booking.id):
        return

    detail = (
        f"rule_version={rule_version} entity_type=BookingRequest "
        f"entity_id={booking.id} reason={audit_action.split(':', 1)[-1]}"
    )
    db.add(AuditLog(
        at=now,
        actor_email=_SYSTEM_ACTOR,
        action=audit_action,
        detail=detail,
    ))
    db.add(Notification(
        user_id=booking.requester_id,
        message=message,
        created_at=now,
    ))
