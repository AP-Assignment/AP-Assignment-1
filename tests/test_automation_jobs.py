# -*- coding: utf-8 -*-
"""
Tests for app.automation.jobs.run_sla_monitoring.

Runs without a Flask app context; uses SQLite in-memory for all DB assertions.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AuditLog,
    Notification,
    User,
    AccessRequest,
    AccessRequestStatusHistory,
    BookingRequest,
)
from app.security import hash_password
from app.automation.jobs import run_sla_monitoring

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0)


@pytest.fixture()
def SessionLocal():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture()
def db(SessionLocal):
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_admin(db, name, email, status="active"):
    u = User(
        name=name,
        email=email,
        password_hash=hash_password("Password1!"),
        team="Team",
        role="admin",
        status=status,
        manager_email="mgr@example.com",
    )
    db.add(u)
    db.flush()
    return u


def _make_user(db, name, email):
    u = User(
        name=name,
        email=email,
        password_hash=hash_password("Password1!"),
        team="Team",
        role="user",
        status="active",
        manager_email="mgr@example.com",
    )
    db.add(u)
    db.flush()
    return u


def _make_access_request(db, requester_id, status="pending", age_hours=0):
    ar = AccessRequest(
        requester_id=requester_id,
        assignment="Test assignment",
        status=status,
        created_at=_NOW - timedelta(hours=age_hours),
    )
    db.add(ar)
    db.flush()
    return ar


def _make_booking_request(db, requester_id, status="pending", age_hours=0):
    br = BookingRequest(
        requester_id=requester_id,
        start_at=_NOW + timedelta(hours=24),
        end_at=_NOW + timedelta(hours=25),
        purpose="Test booking",
        status=status,
        created_at=_NOW - timedelta(hours=age_hours),
    )
    db.add(br)
    db.flush()
    return br


# ---------------------------------------------------------------------------
# Only pending requests are processed
# ---------------------------------------------------------------------------

def test_only_pending_requests_processed(SessionLocal, db):
    """Approved/rejected/expired requests must not trigger any actions."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    # Create requests in non-pending states, all overdue
    for status in ("approved", "rejected", "expired"):
        _make_access_request(db, user.id, status=status, age_hours=200)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AuditLog)).scalars().all() == []
        assert s.execute(select(Notification)).scalars().all() == []


def test_processes_pending_requests(SessionLocal, db):
    """A pending request past the warning threshold must produce audit + notification."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)  # past 8h warning
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1


# ---------------------------------------------------------------------------
# SLA warning produces admin notification and audit
# ---------------------------------------------------------------------------

def test_sla_warning_produces_notification_and_audit(SessionLocal, db):
    """Request older than 8 hours triggers SLA_WARNING_APPROVAL notification."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    ar = _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        assert "SLA_WARNING_APPROVAL" in logs[0].action

        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == admin.id


# ---------------------------------------------------------------------------
# SLA breach produces admin notification and audit
# ---------------------------------------------------------------------------

def test_sla_breach_produces_notification_and_audit(SessionLocal, db):
    """Request older than 48 hours triggers SLA_BREACH_APPROVAL notification."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=50)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        assert "SLA_BREACH_APPROVAL" in logs[0].action

        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == admin.id


# ---------------------------------------------------------------------------
# Auto-expiry: status change + status history + audit + notification
# ---------------------------------------------------------------------------

def test_auto_expiry_sets_status_expired(SessionLocal, db):
    """Request older than 7 days must be expired."""
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()
    ar_id = ar.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed = s.get(AccessRequest, ar_id)
        assert refreshed.status == "expired"


def test_auto_expiry_records_status_history(SessionLocal, db):
    """Auto-expiry must insert an AccessRequestStatusHistory row."""
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()
    ar_id = ar.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        history = s.execute(select(AccessRequestStatusHistory)).scalars().all()
        assert len(history) == 1
        h = history[0]
        assert h.access_request_id == ar_id
        assert h.previous_status == "pending"
        assert h.status == "expired"


def test_auto_expiry_writes_audit_and_notification(SessionLocal, db):
    """Auto-expiry must create two audit entries (STATUS_CHANGE + NOTIFY) and a notification."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 2  # STATUS_CHANGE:AUTO_EXPIRE + automation:AUTO_EXPIRE
        action_types = {log.action for log in logs}
        assert any("STATUS_CHANGE" in a for a in action_types)
        assert any("AUTO_EXPIRE" in a for a in action_types)

        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1


# ---------------------------------------------------------------------------
# Idempotency: running twice must not duplicate audit/notifications
# ---------------------------------------------------------------------------

def test_idempotency_sla_warning(SessionLocal, db):
    """Running run_sla_monitoring twice for SLA warning must not duplicate records."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)
    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert len(s.execute(select(AuditLog)).scalars().all()) == 1
        assert len(s.execute(select(Notification)).scalars().all()) == 1


def test_idempotency_auto_expire(SessionLocal, db):
    """Running run_sla_monitoring twice for auto-expire must not duplicate records."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)
    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        # 2 on first run (STATUS_CHANGE + NOTIFY), none added on second
        assert len(s.execute(select(AuditLog)).scalars().all()) == 2
        assert len(s.execute(select(Notification)).scalars().all()) == 1
        assert len(s.execute(select(AccessRequestStatusHistory)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# No Flask context required
# ---------------------------------------------------------------------------

def test_no_flask_context_required(SessionLocal, db):
    """run_sla_monitoring must work without a Flask application context."""
    import flask
    assert not flask.has_app_context()
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=1)
    db.commit()
    run_sla_monitoring(SessionLocal, now=_NOW)  # should not raise


# ===========================================================================
# BookingRequest SLA automation (Issue #30)
# ===========================================================================

# ---------------------------------------------------------------------------
# Under 8 hours: no action
# ---------------------------------------------------------------------------

def test_booking_request_under_8h_no_action(SessionLocal, db):
    """A pending BookingRequest under the warning threshold must produce no actions."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=7)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AuditLog)).scalars().all() == []
        assert s.execute(select(Notification)).scalars().all() == []


# ---------------------------------------------------------------------------
# At/over 8 hours: SLA warning
# ---------------------------------------------------------------------------

def test_booking_request_sla_warning_at_8h(SessionLocal, db):
    """A pending BookingRequest exactly at 8h must produce SLA_WARNING_APPROVAL."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=8)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        assert "SLA_WARNING_APPROVAL" in logs[0].action
        assert "BookingRequest" in logs[0].detail

        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == admin.id


def test_booking_request_sla_warning_over_8h(SessionLocal, db):
    """A pending BookingRequest over 8h (but under 48h) triggers SLA_WARNING_APPROVAL."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert any("SLA_WARNING_APPROVAL" in log.action for log in logs)
        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1


# ---------------------------------------------------------------------------
# At/over 48 hours: SLA breach
# ---------------------------------------------------------------------------

def test_booking_request_sla_breach_at_48h(SessionLocal, db):
    """A pending BookingRequest exactly at 48h must produce SLA_BREACH_APPROVAL."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=48)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        assert "SLA_BREACH_APPROVAL" in logs[0].action
        assert "BookingRequest" in logs[0].detail

        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == admin.id


def test_booking_request_sla_breach_over_48h(SessionLocal, db):
    """A pending BookingRequest over 48h triggers SLA_BREACH_APPROVAL."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=50)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert any("SLA_BREACH_APPROVAL" in log.action for log in logs)


# ---------------------------------------------------------------------------
# At/over 7 days: auto-expire
# ---------------------------------------------------------------------------

def test_booking_request_auto_expire_at_7d(SessionLocal, db):
    """A pending BookingRequest exactly at 7 days must be set to 'expired'."""
    user = _make_user(db, "Alice", "alice@example.com")
    br = _make_booking_request(db, user.id, status="pending", age_hours=24 * 7)
    db.commit()
    br_id = br.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed = s.get(BookingRequest, br_id)
        assert refreshed.status == "expired"


def test_booking_request_auto_expire_writes_audit_and_notification(SessionLocal, db):
    """Auto-expiry must produce STATUS_CHANGE + NOTIFY audit entries and an admin notification."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 2  # STATUS_CHANGE:AUTO_EXPIRE + automation:AUTO_EXPIRE
        action_types = {log.action for log in logs}
        assert any("STATUS_CHANGE" in a for a in action_types)
        assert any("AUTO_EXPIRE" in a for a in action_types)

        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == admin.id


def test_booking_request_auto_expire_no_status_history(SessionLocal, db):
    """Auto-expiry of BookingRequest must NOT create AccessRequestStatusHistory rows."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AccessRequestStatusHistory)).scalars().all() == []


# ---------------------------------------------------------------------------
# Only pending BookingRequests are processed
# ---------------------------------------------------------------------------

def test_booking_request_only_pending_processed(SessionLocal, db):
    """Non-pending BookingRequests must not trigger any SLA actions."""
    user = _make_user(db, "Alice", "alice@example.com")
    for status in ("approved", "rejected", "cancelled", "expired"):
        _make_booking_request(db, user.id, status=status, age_hours=200)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AuditLog)).scalars().all() == []
        assert s.execute(select(Notification)).scalars().all() == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_booking_request_idempotency_sla_warning(SessionLocal, db):
    """Running run_sla_monitoring twice for BookingRequest SLA warning must not duplicate records."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)
    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert len(s.execute(select(AuditLog)).scalars().all()) == 1
        assert len(s.execute(select(Notification)).scalars().all()) == 1


def test_booking_request_idempotency_auto_expire(SessionLocal, db):
    """Running run_sla_monitoring twice for BookingRequest auto-expire must not duplicate records."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)
    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert len(s.execute(select(AuditLog)).scalars().all()) == 2
        assert len(s.execute(select(Notification)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------

def test_booking_request_boundary_just_under_8h_no_action(SessionLocal, db):
    """7h59m59s pending -> no action (boundary: just below warning threshold)."""
    user = _make_user(db, "Alice", "alice@example.com")
    # 7h 59m 59s = 28799 seconds
    br = BookingRequest(
        requester_id=user.id,
        start_at=_NOW + timedelta(hours=24),
        end_at=_NOW + timedelta(hours=25),
        purpose="Boundary test",
        status="pending",
        created_at=_NOW - timedelta(seconds=28799),
    )
    db.add(br)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AuditLog)).scalars().all() == []


def test_booking_request_boundary_exactly_7d_expires(SessionLocal, db):
    """Exactly 7 days pending -> AUTO_EXPIRE (boundary condition)."""
    user = _make_user(db, "Alice", "alice@example.com")
    br = BookingRequest(
        requester_id=user.id,
        start_at=_NOW + timedelta(hours=24),
        end_at=_NOW + timedelta(hours=25),
        purpose="Boundary test",
        status="pending",
        created_at=_NOW - timedelta(days=7),
    )
    db.add(br)
    db.commit()
    br_id = br.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed = s.get(BookingRequest, br_id)
        assert refreshed.status == "expired"


# ---------------------------------------------------------------------------
# AccessRequest and BookingRequest processed in the same run
# ---------------------------------------------------------------------------

def test_both_entity_types_processed_together(SessionLocal, db):
    """run_sla_monitoring must process both AccessRequest and BookingRequest in one run."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    _make_booking_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        # One SLA_WARNING_APPROVAL audit per entity = 2 total
        assert len(logs) == 2
        detail_texts = {log.detail for log in logs}
        assert any("AccessRequest" in d for d in detail_texts)
        assert any("BookingRequest" in d for d in detail_texts)


# ===========================================================================
# Cascade expiry: BookingRequest expiry expires linked AccessRequest
# ===========================================================================


def _make_linked_access_request(db, requester_id, booking_request_id, status="pending"):
    """Create an AccessRequest linked to a BookingRequest."""
    ar = AccessRequest(
        requester_id=requester_id,
        assignment="Test assignment",
        booking_request_id=booking_request_id,
        status=status,
        created_at=_NOW,
    )
    db.add(ar)
    db.flush()
    return ar


def test_booking_expiry_cascades_to_linked_pending_access_request(SessionLocal, db):
    """When a BookingRequest is auto-expired, its linked pending AccessRequest is also expired."""
    user = _make_user(db, "Alice", "alice@example.com")
    br = _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    ar = _make_linked_access_request(db, user.id, br.id, status="pending")
    db.commit()
    br_id, ar_id = br.id, ar.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed_br = s.get(BookingRequest, br_id)
        refreshed_ar = s.get(AccessRequest, ar_id)
        assert refreshed_br.status == "expired"
        assert refreshed_ar.status == "expired"


def test_booking_expiry_cascade_records_access_request_status_history(SessionLocal, db):
    """Cascade expiry of a linked AccessRequest must add an AccessRequestStatusHistory row."""
    user = _make_user(db, "Alice", "alice@example.com")
    br = _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    ar = _make_linked_access_request(db, user.id, br.id, status="pending")
    db.commit()
    ar_id = ar.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        history = s.execute(select(AccessRequestStatusHistory)).scalars().all()
        assert len(history) == 1
        assert history[0].access_request_id == ar_id
        assert history[0].previous_status == "pending"
        assert history[0].status == "expired"


def test_booking_expiry_cascade_writes_audit_and_notifies_requester(SessionLocal, db):
    """Cascade expiry writes an audit entry and sends a notification to the AR requester."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    br = _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    ar = _make_linked_access_request(db, user.id, br.id, status="pending")
    db.commit()
    ar_id = ar.id

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        # BR: STATUS_CHANGE:AUTO_EXPIRE + automation:AUTO_EXPIRE
        # AR cascade: STATUS_CHANGE:CASCADE_EXPIRE_ACCESS_REQUEST
        assert len(logs) == 3
        actions = {log.action for log in logs}
        assert "automation:STATUS_CHANGE:CASCADE_EXPIRE_ACCESS_REQUEST" in actions

        # Requester (user) gets a notification about the cascade expiry
        notifs = s.execute(select(Notification)).scalars().all()
        # admin gets 1 AUTO_EXPIRE notification; user gets 1 cascade notification
        assert len(notifs) == 2
        user_notif = next(n for n in notifs if n.user_id == user.id)
        assert str(ar_id) in user_notif.message


def test_booking_expiry_no_cascade_when_no_linked_ar(SessionLocal, db):
    """Expiring a BookingRequest without a linked AR must not raise an error."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    # Should not raise
    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        history = s.execute(select(AccessRequestStatusHistory)).scalars().all()
        assert history == []


def test_booking_expiry_does_not_cascade_to_non_pending_linked_ar(SessionLocal, db):
    """A linked AR that is already approved/rejected is not touched by cascade expiry."""
    user = _make_user(db, "Alice", "alice@example.com")
    for ar_status in ("approved", "rejected"):
        br = _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
        _make_linked_access_request(db, user.id, br.id, status=ar_status)
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        # No cascade status history created
        assert s.execute(select(AccessRequestStatusHistory)).scalars().all() == []
        # AR statuses are unchanged
        ars = s.execute(select(AccessRequest)).scalars().all()
        for ar in ars:
            assert ar.status in ("approved", "rejected")


def test_booking_expiry_cascade_idempotency(SessionLocal, db):
    """Running run_sla_monitoring twice must not double-expire the linked AccessRequest."""
    user = _make_user(db, "Alice", "alice@example.com")
    admin = _make_admin(db, "Admin", "admin@example.com")
    br = _make_booking_request(db, user.id, status="pending", age_hours=24 * 8)
    _make_linked_access_request(db, user.id, br.id, status="pending")
    db.commit()

    run_sla_monitoring(SessionLocal, now=_NOW)
    run_sla_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        # Status history must have exactly one entry for the AR
        assert len(s.execute(select(AccessRequestStatusHistory)).scalars().all()) == 1
        # AuditLog: BR STATUS_CHANGE + BR NOTIFY + AR cascade = 3 on first run, no new ones on second
        assert len(s.execute(select(AuditLog)).scalars().all()) == 3


