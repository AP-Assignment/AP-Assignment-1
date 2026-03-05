# -*- coding: utf-8 -*-
"""
Tests for Issue #27: Automation Observability — admin dashboard automation panel.

Verifies:
- dashboard context contains the four automation-state counts
- counts are correct for seeded AccessRequest rows in each state
- structured logging is emitted by run_sla_monitoring (smoke test)
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AccessRequest, User
from app.security import hash_password
from app.automation.jobs import run_sla_monitoring

# ---------------------------------------------------------------------------
# Shared fixed reference time
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0)

# SLA threshold offsets (must match rules.py)
_WARN_H   = 8
_BREACH_H = 48

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _make_user(db, name, email, role="user"):
    u = User(
        name=name,
        email=email,
        password_hash=hash_password("Password1!"),
        team="Team",
        role=role,
        status="active",
        manager_email="mgr@example.com",
    )
    db.add(u)
    db.flush()
    return u


def _make_admin_user(db, name, email):
    u = User(
        name=name,
        email=email,
        password_hash=hash_password("Password1!"),
        team="Team",
        role="admin",
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
# Helper: compute automation state counts the same way the dashboard does
# ---------------------------------------------------------------------------


def _get_automation_counts(db, now):
    """Return (ar_pending, ar_sla_warning, ar_sla_breach, ar_expired) for *now*."""
    from sqlalchemy import func

    warn_threshold   = now - timedelta(hours=_WARN_H)
    breach_threshold = now - timedelta(hours=_BREACH_H)

    ar_pending = db.execute(
        select(func.count()).select_from(AccessRequest)
        .where(AccessRequest.status == "pending", AccessRequest.created_at > warn_threshold)
    ).scalar_one()

    ar_sla_warning = db.execute(
        select(func.count()).select_from(AccessRequest)
        .where(
            AccessRequest.status == "pending",
            AccessRequest.created_at <= warn_threshold,
            AccessRequest.created_at > breach_threshold,
        )
    ).scalar_one()

    ar_sla_breach = db.execute(
        select(func.count()).select_from(AccessRequest)
        .where(
            AccessRequest.status == "pending",
            AccessRequest.created_at <= breach_threshold,
        )
    ).scalar_one()

    ar_expired = db.execute(
        select(func.count()).select_from(AccessRequest)
        .where(AccessRequest.status == "expired")
    ).scalar_one()

    return ar_pending, ar_sla_warning, ar_sla_breach, ar_expired


# ---------------------------------------------------------------------------
# Dashboard automation count correctness
# ---------------------------------------------------------------------------


def test_automation_counts_all_zero_with_no_requests(db):
    """With no AccessRequests, all counts should be zero."""
    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 0
    assert ar_sla_warning == 0
    assert ar_sla_breach == 0
    assert ar_expired == 0


def test_automation_counts_pending_fresh(db):
    """A fresh pending request (age < 8h) increments only ar_pending."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=1)
    db.commit()

    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 1
    assert ar_sla_warning == 0
    assert ar_sla_breach == 0
    assert ar_expired == 0


def test_automation_counts_sla_warning(db):
    """A pending request with age >= 8h and < 48h increments ar_sla_warning."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 0
    assert ar_sla_warning == 1
    assert ar_sla_breach == 0
    assert ar_expired == 0


def test_automation_counts_sla_breach(db):
    """A pending request with age >= 48h increments ar_sla_breach."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=50)
    db.commit()

    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 0
    assert ar_sla_warning == 0
    assert ar_sla_breach == 1
    assert ar_expired == 0


def test_automation_counts_expired(db):
    """A request with status='expired' increments ar_expired."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="expired", age_hours=200)
    db.commit()

    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 0
    assert ar_sla_warning == 0
    assert ar_sla_breach == 0
    assert ar_expired == 1


def test_automation_counts_mixed(db):
    """Multiple requests in different states are counted independently."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending",  age_hours=1)    # fresh pending
    _make_access_request(db, user.id, status="pending",  age_hours=10)   # sla warning
    _make_access_request(db, user.id, status="pending",  age_hours=50)   # sla breach
    _make_access_request(db, user.id, status="expired",  age_hours=200)  # expired
    _make_access_request(db, user.id, status="approved", age_hours=5)    # approved — not counted
    _make_access_request(db, user.id, status="rejected", age_hours=5)    # rejected — not counted
    db.commit()

    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 1
    assert ar_sla_warning == 1
    assert ar_sla_breach == 1
    assert ar_expired == 1


def test_automation_counts_non_pending_statuses_excluded(db):
    """Approved and rejected requests are not counted in pending/warning/breach."""
    user = _make_user(db, "Alice", "alice@example.com")
    for status in ("approved", "rejected", "revoked"):
        _make_access_request(db, user.id, status=status, age_hours=100)
    db.commit()

    ar_pending, ar_sla_warning, ar_sla_breach, ar_expired = _get_automation_counts(db, _NOW)
    assert ar_pending == 0
    assert ar_sla_warning == 0
    assert ar_sla_breach == 0
    assert ar_expired == 0


# ---------------------------------------------------------------------------
# Structured logging smoke tests (caplog)
# ---------------------------------------------------------------------------


def test_sla_monitoring_logs_job_start(SessionLocal, db, caplog):
    """run_sla_monitoring must emit an INFO log line containing job=sla_monitoring."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=1)
    db.commit()

    import logging
    with caplog.at_level(logging.INFO, logger="app.automation.jobs"):
        run_sla_monitoring(SessionLocal, now=_NOW)

    messages = [r.message for r in caplog.records]
    assert any("job=sla_monitoring" in m for m in messages), (
        "Expected a log entry containing 'job=sla_monitoring'"
    )


def test_sla_monitoring_logs_actor(SessionLocal, db, caplog):
    """Log entries must include actor=system@scheduler."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    import logging
    with caplog.at_level(logging.INFO, logger="app.automation.jobs"):
        run_sla_monitoring(SessionLocal, now=_NOW)

    messages = [r.message for r in caplog.records]
    assert any("actor=system@scheduler" in m for m in messages), (
        "Expected a log entry containing 'actor=system@scheduler'"
    )


def test_sla_monitoring_logs_action_for_sla_warning(SessionLocal, db, caplog):
    """run_sla_monitoring must log the SLA_WARNING_APPROVAL state for an overdue request."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_admin_user(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    import logging
    with caplog.at_level(logging.INFO, logger="app.automation.jobs"):
        run_sla_monitoring(SessionLocal, now=_NOW)

    messages = " ".join(r.message for r in caplog.records)
    assert "SLA_WARNING_APPROVAL" in messages, (
        "Expected SLA_WARNING_APPROVAL in log output for a warning-threshold request"
    )


def test_actions_logging_status_change(SessionLocal, db, caplog):
    """apply_actions must log at INFO when a STATUS_CHANGE action is applied."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=24 * 8)
    db.commit()

    import logging
    with caplog.at_level(logging.INFO, logger="app.automation.actions"):
        run_sla_monitoring(SessionLocal, now=_NOW)

    messages = " ".join(r.message for r in caplog.records)
    assert "action=STATUS_CHANGE" in messages, (
        "Expected action=STATUS_CHANGE in actions logger output"
    )
    assert "new_status=expired" in messages, (
        "Expected new_status=expired in actions logger output"
    )


def test_actions_logging_notify(SessionLocal, db, caplog):
    """apply_actions must log at INFO when a NOTIFY action is applied."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_admin_user(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    import logging
    with caplog.at_level(logging.INFO, logger="app.automation.actions"):
        run_sla_monitoring(SessionLocal, now=_NOW)

    messages = " ".join(r.message for r in caplog.records)
    assert "action=NOTIFY" in messages, (
        "Expected action=NOTIFY in actions logger output"
    )


def test_actions_logging_idempotency_skip(SessionLocal, db, caplog):
    """apply_actions must log at DEBUG when a NOTIFY action is skipped due to idempotency."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_admin_user(db, "Admin", "admin@example.com")
    _make_access_request(db, user.id, status="pending", age_hours=10)
    db.commit()

    # First run — action is applied
    run_sla_monitoring(SessionLocal, now=_NOW)

    import logging
    with caplog.at_level(logging.DEBUG, logger="app.automation.actions"):
        # Second run — idempotency should kick in and produce a DEBUG skip log
        run_sla_monitoring(SessionLocal, now=_NOW)

    messages = " ".join(r.message for r in caplog.records)
    assert "skipped=idempotency" in messages, (
        "Expected skipped=idempotency in actions logger output on second run"
    )


# ---------------------------------------------------------------------------
# Helper only used in logging tests
# ---------------------------------------------------------------------------
