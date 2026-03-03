# -*- coding: utf-8 -*-
"""
Tests for app.automation.actions.apply_actions.

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
from app.automation.actions import apply_actions, _DEFAULT_RULE_VERSION
from app.automation.rules import (
    NOTIFY,
    STATUS_CHANGE,
    SLA_WARNING_APPROVAL,
    SLA_BREACH_APPROVAL,
    AUTO_EXPIRE,
    ADMINS,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        yield session


def _make_user(db, name, email, role="user", status="active"):
    u = User(
        name=name,
        email=email,
        password_hash=hash_password("Password1!"),
        team="Team",
        role=role,
        status=status,
        manager_email="mgr@example.com",
    )
    db.add(u)
    db.flush()
    return u


def _make_access_request(db, requester_id, status="pending"):
    ar = AccessRequest(
        requester_id=requester_id,
        assignment="Test assignment",
        status=status,
        created_at=_NOW - timedelta(days=10),
    )
    db.add(ar)
    db.flush()
    return ar


def _make_booking_request(db, requester_id, status="pending"):
    br = BookingRequest(
        requester_id=requester_id,
        start_at=_NOW + timedelta(hours=1),
        end_at=_NOW + timedelta(hours=2),
        purpose="Test",
        status=status,
    )
    db.add(br)
    db.flush()
    return br


# ---------------------------------------------------------------------------
# Empty actions: no DB writes
# ---------------------------------------------------------------------------

def test_empty_actions_no_writes(db):
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id)

    apply_actions(db, ar, [], now=_NOW)

    assert db.execute(select(AuditLog)).scalars().all() == []
    assert db.execute(select(Notification)).scalars().all() == []


# ---------------------------------------------------------------------------
# STATUS_CHANGE — AccessRequest
# ---------------------------------------------------------------------------

def test_status_change_sets_expired_on_access_request(db):
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id, status="pending")

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, ar, actions, now=_NOW)

    assert ar.status == "expired"


def test_status_change_writes_audit_log(db):
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id)

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, ar, actions, now=_NOW)

    logs = db.execute(select(AuditLog)).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_email == "system@scheduler"
    assert log.action == f"automation:STATUS_CHANGE:{AUTO_EXPIRE}"
    assert "entity_id=" in log.detail
    assert "previous_status=pending" in log.detail
    assert "new_status=expired" in log.detail
    assert _DEFAULT_RULE_VERSION in log.detail


def test_status_change_appends_access_request_status_history(db):
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id, status="pending")

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, ar, actions, now=_NOW)

    history = db.execute(select(AccessRequestStatusHistory)).scalars().all()
    assert len(history) == 1
    h = history[0]
    assert h.access_request_id == ar.id
    assert h.previous_status == "pending"
    assert h.status == "expired"
    assert h.changed_by_id is None
    assert AUTO_EXPIRE in h.note


def test_status_change_booking_request_no_history_table(db):
    """BookingRequest status change must NOT create AccessRequestStatusHistory rows."""
    user = _make_user(db, "Alice", "alice@example.com")
    br = _make_booking_request(db, user.id, status="pending")

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, br, actions, now=_NOW)

    assert br.status == "expired"
    assert db.execute(select(AccessRequestStatusHistory)).scalars().all() == []


def test_status_change_skipped_if_already_target_status(db):
    """No audit or history written if status would not actually change."""
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id, status="expired")

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, ar, actions, now=_NOW)

    assert db.execute(select(AuditLog)).scalars().all() == []
    assert db.execute(select(AccessRequestStatusHistory)).scalars().all() == []


# ---------------------------------------------------------------------------
# STATUS_CHANGE idempotency
# ---------------------------------------------------------------------------

def test_status_change_idempotent_second_call_no_duplicate_audit(db):
    """Calling apply_actions twice must not produce a second audit log entry."""
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id, status="pending")

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, ar, actions, now=_NOW)
    apply_actions(db, ar, actions, now=_NOW)

    assert len(db.execute(select(AuditLog)).scalars().all()) == 1
    assert len(db.execute(select(AccessRequestStatusHistory)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# NOTIFY — admin targeting
# ---------------------------------------------------------------------------

def test_notify_queues_notifications_for_active_admins(db):
    admin1 = _make_user(db, "Admin1", "admin1@example.com", role="admin", status="active")
    admin2 = _make_user(db, "Admin2", "admin2@example.com", role="admin", status="active")
    user = _make_user(db, "Requester", "req@example.com", role="user", status="active")
    ar = _make_access_request(db, user.id)

    actions = [{"type": NOTIFY, "reason": SLA_WARNING_APPROVAL, "audience": ADMINS}]
    apply_actions(db, ar, actions, now=_NOW)

    notifs = db.execute(select(Notification)).scalars().all()
    assert len(notifs) == 2
    recipient_ids = {n.user_id for n in notifs}
    assert recipient_ids == {admin1.id, admin2.id}


def test_notify_does_not_queue_for_inactive_admins(db):
    _make_user(db, "InactiveAdmin", "inactive@example.com", role="admin", status="pending")
    user = _make_user(db, "Requester", "req@example.com", role="user", status="active")
    ar = _make_access_request(db, user.id)

    actions = [{"type": NOTIFY, "reason": SLA_WARNING_APPROVAL, "audience": ADMINS}]
    apply_actions(db, ar, actions, now=_NOW)

    assert db.execute(select(Notification)).scalars().all() == []


def test_notify_does_not_queue_for_non_admin_users(db):
    _make_user(db, "Approver", "approver@example.com", role="approver", status="active")
    user = _make_user(db, "Requester", "req@example.com", role="user", status="active")
    ar = _make_access_request(db, user.id)

    actions = [{"type": NOTIFY, "reason": SLA_BREACH_APPROVAL, "audience": ADMINS}]
    apply_actions(db, ar, actions, now=_NOW)

    assert db.execute(select(Notification)).scalars().all() == []


def test_notify_writes_audit_log(db):
    admin = _make_user(db, "Admin", "admin@example.com", role="admin", status="active")
    user = _make_user(db, "Req", "req@example.com")
    ar = _make_access_request(db, user.id)

    actions = [{"type": NOTIFY, "reason": SLA_BREACH_APPROVAL, "audience": ADMINS}]
    apply_actions(db, ar, actions, now=_NOW)

    logs = db.execute(select(AuditLog)).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_email == "system@scheduler"
    assert log.action == f"automation:{SLA_BREACH_APPROVAL}"
    assert _DEFAULT_RULE_VERSION in log.detail
    assert str(ar.id) in log.detail


# ---------------------------------------------------------------------------
# NOTIFY idempotency
# ---------------------------------------------------------------------------

def test_notify_idempotent_second_call_no_duplicate(db):
    admin = _make_user(db, "Admin", "admin@example.com", role="admin", status="active")
    user = _make_user(db, "Req", "req@example.com")
    ar = _make_access_request(db, user.id)

    actions = [{"type": NOTIFY, "reason": SLA_WARNING_APPROVAL, "audience": ADMINS}]
    apply_actions(db, ar, actions, now=_NOW)
    apply_actions(db, ar, actions, now=_NOW)

    assert len(db.execute(select(Notification)).scalars().all()) == 1
    assert len(db.execute(select(AuditLog)).scalars().all()) == 1


def test_notify_idempotent_booking_request(db):
    admin = _make_user(db, "Admin", "admin@example.com", role="admin", status="active")
    user = _make_user(db, "Req", "req@example.com")
    br = _make_booking_request(db, user.id)

    actions = [{"type": NOTIFY, "reason": SLA_WARNING_APPROVAL, "audience": ADMINS}]
    apply_actions(db, br, actions, now=_NOW)
    apply_actions(db, br, actions, now=_NOW)

    assert len(db.execute(select(Notification)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Combined AUTO_EXPIRE actions (STATUS_CHANGE + NOTIFY)
# ---------------------------------------------------------------------------

def test_auto_expire_applies_both_status_and_notification(db):
    admin = _make_user(db, "Admin", "admin@example.com", role="admin", status="active")
    user = _make_user(db, "Req", "req@example.com")
    ar = _make_access_request(db, user.id, status="pending")

    actions = [
        {"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"},
        {"type": NOTIFY, "reason": AUTO_EXPIRE, "audience": ADMINS},
    ]
    apply_actions(db, ar, actions, now=_NOW)

    assert ar.status == "expired"
    assert len(db.execute(select(Notification)).scalars().all()) == 1
    # Two audit log entries: one for STATUS_CHANGE and one for NOTIFY
    logs = db.execute(select(AuditLog)).scalars().all()
    assert len(logs) == 2


def test_auto_expire_idempotent_combined(db):
    admin = _make_user(db, "Admin", "admin@example.com", role="admin", status="active")
    user = _make_user(db, "Req", "req@example.com")
    ar = _make_access_request(db, user.id, status="pending")

    actions = [
        {"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"},
        {"type": NOTIFY, "reason": AUTO_EXPIRE, "audience": ADMINS},
    ]
    apply_actions(db, ar, actions, now=_NOW)
    apply_actions(db, ar, actions, now=_NOW)

    assert len(db.execute(select(AuditLog)).scalars().all()) == 2
    assert len(db.execute(select(Notification)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# custom rule_version
# ---------------------------------------------------------------------------

def test_custom_rule_version_in_audit(db):
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id)

    actions = [{"type": STATUS_CHANGE, "reason": AUTO_EXPIRE, "new_status": "expired"}]
    apply_actions(db, ar, actions, now=_NOW, rule_version="automation_rules_v2.0")

    log = db.execute(select(AuditLog)).scalars().first()
    assert "automation_rules_v2.0" in log.detail


# ---------------------------------------------------------------------------
# No Flask context required
# ---------------------------------------------------------------------------

def test_no_flask_context_required(db):
    """apply_actions must work without a Flask application context."""
    import flask
    assert not flask.has_app_context()
    user = _make_user(db, "Alice", "alice@example.com")
    ar = _make_access_request(db, user.id)
    apply_actions(db, ar, [], now=_NOW)  # should not raise
