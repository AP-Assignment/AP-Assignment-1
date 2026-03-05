# -*- coding: utf-8 -*-
"""
Tests for app.automation.jobs.run_access_window_monitoring (Issue #25).

Runs without a Flask app context; uses SQLite in-memory for all DB assertions.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AuditLog, BookingRequest, Notification, User
from app.security import hash_password
from app.automation.jobs import run_access_window_monitoring

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


def _make_booking(db, requester_id, *, start_at, end_at, status="approved",
                  checked_in=False, no_show=False):
    b = BookingRequest(
        requester_id=requester_id,
        start_at=start_at,
        end_at=end_at,
        purpose="Test booking",
        status=status,
        checked_in=checked_in,
        no_show=no_show,
    )
    db.add(b)
    db.flush()
    return b


# ---------------------------------------------------------------------------
# Only approved bookings are processed
# ---------------------------------------------------------------------------

def test_only_approved_bookings_processed(SessionLocal, db):
    """Non-approved bookings (pending, rejected, cancelled) must not trigger any actions."""
    user = _make_user(db, "Alice", "alice@example.com")
    # Booking ended long ago (missed) but not approved
    for status in ("pending", "rejected", "cancelled"):
        _make_booking(
            db, user.id,
            start_at=_NOW - timedelta(hours=3),
            end_at=_NOW - timedelta(hours=2),
            status=status,
        )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AuditLog)).scalars().all() == []
        assert s.execute(select(Notification)).scalars().all() == []


# ---------------------------------------------------------------------------
# Missed / no-show detection
# ---------------------------------------------------------------------------

def test_missed_window_sets_no_show(SessionLocal, db):
    """Approved booking past end_at with no check-in must be marked no_show=True."""
    user = _make_user(db, "Alice", "alice@example.com")
    b = _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        status="approved",
        checked_in=False,
        no_show=False,
    )
    db.commit()
    booking_id = b.id

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed = s.get(BookingRequest, booking_id)
        assert refreshed.no_show is True


def test_missed_window_writes_audit(SessionLocal, db):
    """Missed booking must write a NO_SHOW_MARKED audit entry."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        assert "NO_SHOW_MARKED" in logs[0].action
        assert logs[0].actor_email == "system@scheduler"


def test_missed_window_writes_user_notification(SessionLocal, db):
    """Missed booking must write a notification to the requester."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == user.id


def test_missed_window_idempotent(SessionLocal, db):
    """Running twice for a missed booking must not duplicate audit entries or notifications."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)
    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert len(s.execute(select(AuditLog)).scalars().all()) == 1
        assert len(s.execute(select(Notification)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Active windows do not mark no-show
# ---------------------------------------------------------------------------

def test_active_window_does_not_set_no_show(SessionLocal, db):
    """A booking whose window is currently active must not be marked no_show."""
    user = _make_user(db, "Alice", "alice@example.com")
    b = _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=1),
        end_at=_NOW + timedelta(hours=1),
        status="approved",
        checked_in=False,
        no_show=False,
    )
    db.commit()
    booking_id = b.id

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed = s.get(BookingRequest, booking_id)
        assert refreshed.no_show is False
        # Active windows produce no audit events
        assert s.execute(select(AuditLog)).scalars().all() == []


# ---------------------------------------------------------------------------
# Starting-soon detection
# ---------------------------------------------------------------------------

def test_starting_soon_writes_audit(SessionLocal, db):
    """Booking starting within soon_minutes must write a BOOKING_WINDOW_STARTING_SOON audit."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW + timedelta(minutes=10),   # 10 min away; within 15-min default
        end_at=_NOW + timedelta(hours=1),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        logs = s.execute(select(AuditLog)).scalars().all()
        assert len(logs) == 1
        assert "BOOKING_WINDOW_STARTING_SOON" in logs[0].action
        assert logs[0].actor_email == "system@scheduler"


def test_starting_soon_writes_user_notification(SessionLocal, db):
    """Starting-soon event must notify the booking requester."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW + timedelta(minutes=10),
        end_at=_NOW + timedelta(hours=1),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        notifs = s.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id == user.id


def test_starting_soon_idempotent(SessionLocal, db):
    """Running twice for a starting-soon booking must not duplicate audit or notifications."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW + timedelta(minutes=10),
        end_at=_NOW + timedelta(hours=1),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)
    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert len(s.execute(select(AuditLog)).scalars().all()) == 1
        assert len(s.execute(select(Notification)).scalars().all()) == 1


def test_not_starting_soon_outside_window(SessionLocal, db):
    """Booking starting more than soon_minutes away must not trigger starting-soon."""
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW + timedelta(hours=2),   # far in the future
        end_at=_NOW + timedelta(hours=3),
        status="approved",
    )
    db.commit()

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        assert s.execute(select(AuditLog)).scalars().all() == []
        assert s.execute(select(Notification)).scalars().all() == []


# ---------------------------------------------------------------------------
# Checked-in bookings are not marked no-show
# ---------------------------------------------------------------------------

def test_checked_in_booking_not_marked_no_show(SessionLocal, db):
    """A booking that is past end_at but checked_in=True must not set no_show."""
    user = _make_user(db, "Alice", "alice@example.com")
    b = _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        status="approved",
        checked_in=True,
        no_show=False,
    )
    db.commit()
    booking_id = b.id

    run_access_window_monitoring(SessionLocal, now=_NOW)

    with SessionLocal() as s:
        refreshed = s.get(BookingRequest, booking_id)
        assert refreshed.no_show is False


# ---------------------------------------------------------------------------
# Scheduler registration test
# ---------------------------------------------------------------------------

def test_access_window_monitoring_job_registered():
    """access_window_monitoring job must be registered with correct interval and settings."""
    from app import create_app
    import app as app_module

    original = app_module._scheduler_started
    app_module._scheduler_started = False
    try:
        application = create_app()
        application.config["TESTING"] = True

        job = application.scheduler.get_job("access_window_monitoring")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 60
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time == 60
    finally:
        app_module._scheduler_started = original


# ---------------------------------------------------------------------------
# No Flask context required
# ---------------------------------------------------------------------------

def test_no_flask_context_required(SessionLocal, db):
    """run_access_window_monitoring must work without a Flask application context."""
    import flask
    assert not flask.has_app_context()
    user = _make_user(db, "Alice", "alice@example.com")
    _make_booking(
        db, user.id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        status="approved",
    )
    db.commit()
    run_access_window_monitoring(SessionLocal, now=_NOW)  # should not raise
