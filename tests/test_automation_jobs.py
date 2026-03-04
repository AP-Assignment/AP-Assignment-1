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
