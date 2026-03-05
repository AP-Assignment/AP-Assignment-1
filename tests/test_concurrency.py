# -*- coding: utf-8 -*-
"""
Tests for safe database interaction under concurrency (Issue #26).

Verifies that:
- Running the same job twice sequentially does not produce duplicate
  audit entries or notifications (repeat-execution safety).
- Running the same job from two threads simultaneously does not produce
  duplicates (thread-concurrency safety).

These tests use a file-based SQLite database with ``check_same_thread=False``
so that multiple threads share the same engine without raising thread-safety
errors.  SQLite serialises concurrent writes, so the idempotency guards in
each job (AuditLog-based dedup) are the primary protection against duplicate
records.

No Flask app context required.
"""

from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AuditLog, AccessRequest, BookingRequest, Notification, User
from app.security import hash_password
from app.automation.jobs import run_sla_monitoring, run_access_window_monitoring

# ---------------------------------------------------------------------------
# Fixed clock used in all tests for deterministic SLA evaluation
# ---------------------------------------------------------------------------
_NOW = datetime(2024, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Fixtures: file-based SQLite so threads can share an engine
# ---------------------------------------------------------------------------

@pytest.fixture()
def file_db_factory(tmp_path):
    """Yield a sessionmaker backed by a temporary file-based SQLite database.

    ``check_same_thread=False`` is required so that the engine can be used
    safely from multiple threads in the concurrency tests below.
    """
    db_path = str(tmp_path / "test_concurrency.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture()
def setup_session(file_db_factory):
    """Open a setup session, yield it, then commit and close."""
    session = file_db_factory()
    yield session
    session.commit()
    session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_admin(db, name="Admin", email="admin@example.com"):
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


def _make_user(db, name="Alice", email="alice@example.com"):
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


def _make_pending_access_request(db, requester_id, age_hours=10):
    ar = AccessRequest(
        requester_id=requester_id,
        assignment="Test assignment",
        status="pending",
        created_at=_NOW - timedelta(hours=age_hours),
    )
    db.add(ar)
    db.flush()
    return ar


def _make_missed_booking(db, requester_id):
    b = BookingRequest(
        requester_id=requester_id,
        start_at=_NOW - timedelta(hours=3),
        end_at=_NOW - timedelta(hours=2),
        purpose="Test booking",
        status="approved",
        checked_in=False,
        no_show=False,
    )
    db.add(b)
    db.flush()
    return b


# ---------------------------------------------------------------------------
# Repeated sequential execution: SLA monitoring
# ---------------------------------------------------------------------------

def test_sla_monitoring_no_duplicates_on_repeated_execution(file_db_factory, setup_session):
    """Running run_sla_monitoring twice must not produce duplicate records.

    This is the core idempotency contract: the AuditLog-based dedup guard
    ensures that the second run recognises the audit entry from the first run
    and skips writing again.
    """
    _make_admin(setup_session)
    user = _make_user(setup_session)
    _make_pending_access_request(setup_session, user.id, age_hours=10)
    setup_session.commit()

    # First run
    run_sla_monitoring(file_db_factory, now=_NOW)
    # Second run – must not add anything
    run_sla_monitoring(file_db_factory, now=_NOW)

    with file_db_factory() as s:
        audit_count = len(s.execute(select(AuditLog)).scalars().all())
        notif_count = len(s.execute(select(Notification)).scalars().all())

    assert audit_count == 1, f"Expected 1 audit entry, got {audit_count}"
    assert notif_count == 1, f"Expected 1 notification, got {notif_count}"


def test_sla_auto_expire_no_duplicates_on_repeated_execution(file_db_factory, setup_session):
    """Auto-expire via run_sla_monitoring must not duplicate records on second run."""
    _make_admin(setup_session)
    user = _make_user(setup_session)
    _make_pending_access_request(setup_session, user.id, age_hours=24 * 8)
    setup_session.commit()

    run_sla_monitoring(file_db_factory, now=_NOW)
    run_sla_monitoring(file_db_factory, now=_NOW)

    with file_db_factory() as s:
        audit_count = len(s.execute(select(AuditLog)).scalars().all())
        notif_count = len(s.execute(select(Notification)).scalars().all())

    # First run produces 2 audits (STATUS_CHANGE + NOTIFY); second run adds none
    assert audit_count == 2, f"Expected 2 audit entries, got {audit_count}"
    assert notif_count == 1, f"Expected 1 notification, got {notif_count}"


# ---------------------------------------------------------------------------
# Repeated sequential execution: access window monitoring
# ---------------------------------------------------------------------------

def test_access_window_no_show_no_duplicates_on_repeated_execution(file_db_factory, setup_session):
    """Running run_access_window_monitoring twice for a missed booking must not duplicate records."""
    user = _make_user(setup_session)
    _make_missed_booking(setup_session, user.id)
    setup_session.commit()

    run_access_window_monitoring(file_db_factory, now=_NOW)
    run_access_window_monitoring(file_db_factory, now=_NOW)

    with file_db_factory() as s:
        audit_count = len(s.execute(select(AuditLog)).scalars().all())
        notif_count = len(s.execute(select(Notification)).scalars().all())

    assert audit_count == 1, f"Expected 1 audit entry, got {audit_count}"
    assert notif_count == 1, f"Expected 1 notification, got {notif_count}"


def test_access_window_starting_soon_no_duplicates_on_repeated_execution(
    file_db_factory, setup_session
):
    """Running run_access_window_monitoring twice for a starting-soon booking must not duplicate."""
    user = _make_user(setup_session)
    b = BookingRequest(
        requester_id=user.id,
        start_at=_NOW + timedelta(minutes=10),
        end_at=_NOW + timedelta(hours=1),
        purpose="Test",
        status="approved",
        checked_in=False,
        no_show=False,
    )
    setup_session.add(b)
    setup_session.flush()
    setup_session.commit()

    run_access_window_monitoring(file_db_factory, now=_NOW)
    run_access_window_monitoring(file_db_factory, now=_NOW)

    with file_db_factory() as s:
        audit_count = len(s.execute(select(AuditLog)).scalars().all())
        notif_count = len(s.execute(select(Notification)).scalars().all())

    assert audit_count == 1, f"Expected 1 audit entry, got {audit_count}"
    assert notif_count == 1, f"Expected 1 notification, got {notif_count}"


# ---------------------------------------------------------------------------
# Concurrent execution: two threads run the same job simultaneously
# ---------------------------------------------------------------------------

def test_sla_monitoring_concurrent_no_duplicates(file_db_factory, setup_session):
    """Two threads running run_sla_monitoring simultaneously must not produce duplicates.

    Concurrency safety relies on:
    1. AuditLog-based idempotency guard: the second thread to commit will find
       the audit entry written by the first thread and skip writing again.
    2. SQLite's write serialisation: only one writer proceeds at a time,
       ensuring the dedup check is consistent.
    """
    _make_admin(setup_session)
    user = _make_user(setup_session)
    _make_pending_access_request(setup_session, user.id, age_hours=10)
    setup_session.commit()

    errors_list: list = []
    barrier = threading.Barrier(2)

    def thread_body():
        barrier.wait(timeout=5)
        try:
            run_sla_monitoring(file_db_factory, now=_NOW)
        except Exception as exc:  # pragma: no cover
            errors_list.append(exc)

    threads = [threading.Thread(target=thread_body) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors_list, f"Thread raised exceptions: {errors_list}"

    with file_db_factory() as s:
        audit_count = len(s.execute(select(AuditLog)).scalars().all())
        notif_count = len(s.execute(select(Notification)).scalars().all())

    assert audit_count == 1, f"Expected 1 audit entry after concurrent run, got {audit_count}"
    assert notif_count == 1, f"Expected 1 notification after concurrent run, got {notif_count}"


def test_access_window_monitoring_concurrent_no_duplicates(file_db_factory, setup_session):
    """Two threads running run_access_window_monitoring simultaneously must not produce duplicates."""
    user = _make_user(setup_session)
    _make_missed_booking(setup_session, user.id)
    setup_session.commit()

    errors_list: list = []
    barrier = threading.Barrier(2)

    def thread_body():
        barrier.wait(timeout=5)
        try:
            run_access_window_monitoring(file_db_factory, now=_NOW)
        except Exception as exc:  # pragma: no cover
            errors_list.append(exc)

    threads = [threading.Thread(target=thread_body) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors_list, f"Thread raised exceptions: {errors_list}"

    with file_db_factory() as s:
        audit_count = len(s.execute(select(AuditLog)).scalars().all())
        notif_count = len(s.execute(select(Notification)).scalars().all())

    assert audit_count == 1, f"Expected 1 audit entry after concurrent run, got {audit_count}"
    assert notif_count == 1, f"Expected 1 notification after concurrent run, got {notif_count}"
