# -*- coding: utf-8 -*-
"""
Scheduled background jobs: Overdue & SLA Monitoring (Issue #24),
Access Window Monitoring (Issue #25).

Issue #24: Queries pending AccessRequest rows, evaluates SLA rules via the
rule engine, and applies any resulting actions through the action handler.

Issue #25: Monitors booking windows for approved BookingRequests, detecting
starting-soon, active, and missed states, and records audit events.

Concurrency safety (Issue #26)
-------------------------------
Both jobs use ``job_session`` from ``job_utils`` to obtain a single
short-lived session per execution.  AuditLog-based idempotency guards
prevent duplicate writes if the same job fires more than once for the same
entity (e.g. due to a process restart or overlapping test runs).  See
``app/automation/job_utils.py`` for the full concurrency strategy.

No Flask app context required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..models import AccessRequest, AuditLog, BookingRequest, Notification, User
from .rules import evaluate_request
from .actions import apply_actions
from .job_utils import job_session

_DEFAULT_RULE_VERSION = "automation_rules_v1.1"
_SYSTEM_ACTOR = "system@scheduler"
_logger = logging.getLogger(__name__)


def run_sla_monitoring(
    SessionLocal,
    *,
    now: Optional[datetime] = None,
    rule_version: str = _DEFAULT_RULE_VERSION,
) -> None:
    """Evaluate SLA rules for all pending AccessRequests and BookingRequests and apply actions.

    Processes both entity types within a single session:
    - Pending ``AccessRequest`` rows (existing behaviour)
    - Pending ``BookingRequest`` rows (Issue #30)

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

    with job_session(SessionLocal, job_name="sla_monitoring") as db:
        # --- AccessRequest ---
        ar_requests = db.execute(
            select(AccessRequest).where(AccessRequest.status == "pending")
        ).scalars().all()

        _logger.info(
            "job=sla_monitoring actor=%s entity_type=AccessRequest entity_count=%d",
            _SYSTEM_ACTOR, len(ar_requests),
        )

        ar_actions_applied = 0
        for request in ar_requests:
            result = evaluate_request(now, request, entity_type="AccessRequest")
            if result["actions"]:
                reasons = [a.get("reason", "") for a in result["actions"]]
                _logger.info(
                    "job=sla_monitoring actor=%s entity_type=AccessRequest entity_id=%s state=%s",
                    _SYSTEM_ACTOR, request.id, ",".join(reasons),
                )
                ar_actions_applied += 1
            apply_actions(db, request, result["actions"], now=now, rule_version=rule_version)

        _logger.info(
            "job=sla_monitoring actor=%s entity_type=AccessRequest completed evaluated=%d with_actions=%d",
            _SYSTEM_ACTOR, len(ar_requests), ar_actions_applied,
        )

        # --- BookingRequest (Issue #30) ---
        br_requests = db.execute(
            select(BookingRequest).where(BookingRequest.status == "pending")
        ).scalars().all()

        _logger.info(
            "job=sla_monitoring actor=%s entity_type=BookingRequest entity_count=%d",
            _SYSTEM_ACTOR, len(br_requests),
        )

        br_actions_applied = 0
        for request in br_requests:
            result = evaluate_request(now, request, entity_type="BookingRequest")
            if result["actions"]:
                reasons = [a.get("reason", "") for a in result["actions"]]
                _logger.info(
                    "job=sla_monitoring actor=%s entity_type=BookingRequest entity_id=%s state=%s",
                    _SYSTEM_ACTOR, request.id, ",".join(reasons),
                )
                br_actions_applied += 1
            apply_actions(db, request, result["actions"], now=now, rule_version=rule_version)

        _logger.info(
            "job=sla_monitoring actor=%s entity_type=BookingRequest completed evaluated=%d with_actions=%d",
            _SYSTEM_ACTOR, len(br_requests), br_actions_applied,
        )


_NO_SHOW_GRACE_MINUTES = 5


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
    - **Grace period**: ``start_at <= now <= start_at + 5 minutes``
      → no action; user still has time to check in.
    - **No-show** (Issue #31): ``now > start_at + 5 minutes`` and not
      checked-in and not already marked no-show → sets ``no_show = True``,
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

    with job_session(SessionLocal, job_name="access_window_monitoring") as db:
        bookings = db.execute(
            select(BookingRequest).where(
                BookingRequest.status == "approved",
                BookingRequest.start_at >= now - horizon,
                BookingRequest.start_at <= now + horizon,
            )
        ).scalars().all()

        _logger.info(
            "job=access_window_monitoring actor=%s entity_type=BookingRequest entity_count=%d",
            _SYSTEM_ACTOR, len(bookings),
        )

        starting_soon = 0
        no_shows = 0
        for booking in bookings:
            soon_threshold = booking.start_at - timedelta(minutes=soon_minutes)
            no_show_threshold = booking.start_at + timedelta(minutes=_NO_SHOW_GRACE_MINUTES)

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
                starting_soon += 1
                _logger.info(
                    "job=access_window_monitoring actor=%s entity_id=%s state=starting_soon",
                    _SYSTEM_ACTOR, booking.id,
                )

            elif now > no_show_threshold:
                # Grace period has elapsed since start_at (Issue #31)
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
                    no_shows += 1
                    _logger.info(
                        "job=access_window_monitoring actor=%s entity_id=%s state=no_show action=NO_SHOW_MARKED",
                        _SYSTEM_ACTOR, booking.id,
                    )

        _logger.info(
            "job=access_window_monitoring actor=%s completed evaluated=%d "
            "starting_soon=%d no_shows=%d",
            _SYSTEM_ACTOR, len(bookings), starting_soon, no_shows,
        )


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
