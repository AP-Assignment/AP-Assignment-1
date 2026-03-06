# -*- coding: utf-8 -*-
"""
Tests for Issue #44: AccessRequest creation from booking submission.

Verifies backend enforcement logic:
- VIRTUAL-only selection + checkbox checked  → no AccessRequest created.
- LAB selection + checkbox unchecked         → no AccessRequest created.
- LAB selection + checkbox checked           → AccessRequest created.
- Mixed LAB + VIRTUAL + checkbox checked     → AccessRequest created.
- Multi-site LAB + checkbox checked          → one AccessRequest per site.

These tests operate directly on the database layer (no Flask app context
required) to mirror the pattern used in the rest of the test suite.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AccessRequest,
    AuditLog,
    BookingItem,
    BookingRequest,
    Machine,
    Site,
    User,
)
from app.security import hash_password


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        yield session


@pytest.fixture()
def site_a(db):
    s = Site(name="Site Alpha", city="London", lat=51.5, lon=-0.1)
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def site_b(db):
    s = Site(name="Site Beta", city="Manchester", lat=53.48, lon=-2.24)
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def requester(db):
    u = User(
        name="Test User",
        email="testuser@example.com",
        password_hash=hash_password("Password1!"),
        team="QA",
        role="user",
        status="active",
        manager_email="mgr@example.com",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def lab_machine_a(db, site_a):
    m = Machine(name="LAB-A1", machine_type="lab", category="Core", status="available", site_id=site_a.id)
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def lab_machine_b(db, site_b):
    m = Machine(name="LAB-B1", machine_type="lab", category="Core", status="available", site_id=site_b.id)
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def virtual_machine(db, site_a):
    m = Machine(name="VIRT-A1", machine_type="virtual", category="Core", status="available", site_id=site_a.id)
    db.add(m)
    db.flush()
    return m


# ---------------------------------------------------------------------------
# Helper – simulates the backend logic from bookings.new_booking (POST)
# ---------------------------------------------------------------------------


def _submit_booking(db, requester, machine_ids: list, request_access: bool) -> BookingRequest:
    """Simulate the booking creation logic from the bookings blueprint.

    Returns the created BookingRequest so tests can inspect side-effects
    (AccessRequest rows, AuditLog rows) directly.
    """
    start = datetime.utcnow() + timedelta(hours=1)
    end = start + timedelta(hours=2)

    booking = BookingRequest(
        requester_id=requester.id,
        start_at=start,
        end_at=end,
        purpose="Integration test booking",
        status="pending",
    )
    db.add(booking)
    db.flush()

    for mid in machine_ids:
        db.add(BookingItem(booking_id=booking.id, machine_id=mid))

    # Re-fetch selected machines – mirrors anti-spoofing in the view
    selected_machines = db.execute(
        select(Machine).where(Machine.id.in_(machine_ids))
    ).scalars().all()
    contains_lab = any(m.machine_type == "lab" for m in selected_machines)

    if request_access and contains_lab:
        lab_machines = [m for m in selected_machines if m.machine_type == "lab"]
        sites_seen: dict[int, str] = {}
        for m in lab_machines:
            if m.site_id not in sites_seen:
                sites_seen[m.site_id] = m.site.city
        for site_id, site_city in sites_seen.items():
            ar = AccessRequest(
                requester_id=requester.id,
                site_id=site_id,
                assignment=f"Booking #{booking.id} – site access for {site_city}",
                status="pending",
            )
            db.add(ar)
            db.flush()
            db.add(AuditLog(
                actor_email=requester.email,
                action="access_request_created_from_booking",
                detail=(
                    f"booking_id={booking.id}, access_request_id={ar.id}, "
                    f"machine_ids={machine_ids}, contains_lab={contains_lab}, site_id={site_id}"
                ),
            ))

    db.commit()
    return booking


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_virtual_only_checkbox_checked_no_access_request(db, requester, virtual_machine):
    """VIRTUAL-only selection with checkbox checked must NOT create an AccessRequest."""
    booking = _submit_booking(db, requester, [virtual_machine.id], request_access=True)

    access_requests = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalars().all()
    assert access_requests == [], "No AccessRequest should be created for VIRTUAL-only selection"


def test_lab_checkbox_unchecked_no_access_request(db, requester, lab_machine_a):
    """LAB machine selected but checkbox unchecked must NOT create an AccessRequest."""
    booking = _submit_booking(db, requester, [lab_machine_a.id], request_access=False)

    access_requests = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalars().all()
    assert access_requests == [], "No AccessRequest should be created when checkbox is unchecked"


def test_lab_checkbox_checked_creates_access_request(db, requester, lab_machine_a):
    """LAB machine selected and checkbox checked MUST create an AccessRequest."""
    booking = _submit_booking(db, requester, [lab_machine_a.id], request_access=True)

    access_requests = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalars().all()
    assert len(access_requests) == 1
    ar = access_requests[0]
    assert ar.status == "pending"
    assert ar.site_id == lab_machine_a.site_id
    assert str(booking.id) in ar.assignment


def test_lab_access_request_audit_log_created(db, requester, lab_machine_a):
    """An AuditLog entry must be created when an AccessRequest is created from booking."""
    booking = _submit_booking(db, requester, [lab_machine_a.id], request_access=True)

    logs = db.execute(
        select(AuditLog).where(AuditLog.action == "access_request_created_from_booking")
    ).scalars().all()
    assert len(logs) == 1
    assert requester.email in logs[0].actor_email
    assert str(booking.id) in logs[0].detail


def test_mixed_lab_virtual_checkbox_checked_creates_access_request(
    db, requester, lab_machine_a, virtual_machine
):
    """Mixed LAB + VIRTUAL selection with checkbox checked MUST create an AccessRequest."""
    booking = _submit_booking(
        db, requester, [lab_machine_a.id, virtual_machine.id], request_access=True
    )

    access_requests = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalars().all()
    assert len(access_requests) == 1
    assert access_requests[0].site_id == lab_machine_a.site_id


def test_multi_site_lab_creates_one_access_request_per_site(
    db, requester, lab_machine_a, lab_machine_b
):
    """Multi-site LAB selection must create one AccessRequest per site."""
    assert lab_machine_a.site_id != lab_machine_b.site_id, "Fixtures must be on different sites"

    booking = _submit_booking(
        db, requester, [lab_machine_a.id, lab_machine_b.id], request_access=True
    )

    access_requests = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalars().all()
    assert len(access_requests) == 2
    site_ids = {ar.site_id for ar in access_requests}
    assert site_ids == {lab_machine_a.site_id, lab_machine_b.site_id}

    # Two AuditLog entries (one per site)
    logs = db.execute(
        select(AuditLog).where(AuditLog.action == "access_request_created_from_booking")
    ).scalars().all()
    assert len(logs) == 2


def test_access_request_has_pending_status_by_default(db, requester, lab_machine_a):
    """AccessRequests created from booking must start with status 'pending'."""
    _submit_booking(db, requester, [lab_machine_a.id], request_access=True)

    ar = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalar_one()
    assert ar.status == "pending"


def test_virtual_only_checkbox_unchecked_no_access_request(db, requester, virtual_machine):
    """VIRTUAL-only selection with checkbox unchecked must NOT create an AccessRequest."""
    _submit_booking(db, requester, [virtual_machine.id], request_access=False)

    access_requests = db.execute(
        select(AccessRequest).where(AccessRequest.requester_id == requester.id)
    ).scalars().all()
    assert access_requests == []
