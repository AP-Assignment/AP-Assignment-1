# -*- coding: utf-8 -*-
"""
Tests for the database initialization and seed-data script (seed.py).

Each test uses a temporary on-disk SQLite file so that the engine created
inside seed() and the engine used by the test session share the same data.
(SQLite in-memory databases are per-connection; sharing state across two
engine objects requires a named file.)
"""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, select, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AccessRequest,
    AccessRequestStatusHistory,
    Assignment,
    AssignmentApprover,
    Location,
    Machine,
    Site,
    User,
)
from seed import seed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """Provide a seeded session backed by a temporary SQLite file.

    A temporary file is used instead of an in-memory database so that the
    engine created inside seed() and the engine created here both read from
    the same on-disk database.  The file is deleted after the test.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"
    try:
        seed(db_url)
        engine = create_engine(
            db_url,
            future=True,
            connect_args={"check_same_thread": False},
        )
        SessionLocal = sessionmaker(bind=engine, future=True)
        with SessionLocal() as session:
            yield session, engine
        engine.dispose()
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_seed_is_idempotent():
    """Calling seed() twice must not create duplicate rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"
    try:
        seed(db_url)
        seed(db_url)  # second call must be a no-op

        engine = create_engine(
            db_url, future=True, connect_args={"check_same_thread": False}
        )
        SessionLocal = sessionmaker(bind=engine, future=True)
        with SessionLocal() as session:
            assert len(session.execute(select(Site)).scalars().all()) == 5
            assert len(session.execute(select(User)).scalars().all()) == 3
            assert len(session.execute(select(AccessRequest)).scalars().all()) == 5
        engine.dispose()
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_seed_creates_all_tables(db):
    """seed() must create all required tables."""
    _, engine = db
    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())
    for expected in (
        "sites",
        "locations",
        "machines",
        "users",
        "assignments",
        "assignment_approvers",
        "access_requests",
        "access_request_status_history",
    ):
        assert expected in tables, f"Table '{expected}' is missing after seed()"


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

def test_seed_creates_five_sites(db):
    session, _ = db
    sites = session.execute(select(Site)).scalars().all()
    assert len(sites) == 5


def test_seed_site_codes_are_unique(db):
    session, _ = db
    codes = [s.code for s in session.execute(select(Site)).scalars().all()]
    assert len(codes) == len(set(codes))


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def test_seed_creates_locations_for_every_site(db):
    """Each site must have at least 2 locations (lab + virtual lab)."""
    session, _ = db
    sites = session.execute(select(Site)).scalars().all()
    for site in sites:
        session.refresh(site)
        assert len(site.locations) >= 2, (
            f"Site '{site.name}' has fewer than 2 locations"
        )


def test_seed_sub_location_has_parent(db):
    """Bay-A sub-locations must have a non-null parent_id."""
    session, _ = db
    sub_locs = (
        session.execute(select(Location).where(Location.code == "LAB-A"))
        .scalars()
        .all()
    )
    assert len(sub_locs) == 5  # one per site
    for loc in sub_locs:
        assert loc.parent_id is not None


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------

def test_seed_creates_one_hundred_machines(db):
    session, _ = db
    count = len(session.execute(select(Machine)).scalars().all())
    assert count == 100


def test_seed_machine_names_are_unique(db):
    session, _ = db
    names = [m.name for m in session.execute(select(Machine)).scalars().all()]
    assert len(names) == len(set(names))


def test_seed_machines_have_valid_types(db):
    session, _ = db
    types = {
        m.machine_type
        for m in session.execute(select(Machine)).scalars().all()
    }
    assert types.issubset({"lab", "virtual"})


def test_seed_machine_statuses_are_valid(db):
    session, _ = db
    statuses = {
        m.status for m in session.execute(select(Machine)).scalars().all()
    }
    assert statuses.issubset({"available", "out_of_service"})


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_seed_creates_three_users(db):
    session, _ = db
    users = session.execute(select(User)).scalars().all()
    assert len(users) == 3


def test_seed_user_roles_cover_all_role_types(db):
    session, _ = db
    roles = {u.role for u in session.execute(select(User)).scalars().all()}
    assert roles == {"admin", "approver", "user"}


def test_seed_users_are_active(db):
    session, _ = db
    statuses = {
        u.status for u in session.execute(select(User)).scalars().all()
    }
    assert statuses == {"active"}


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

def test_seed_creates_three_assignments(db):
    session, _ = db
    assignments = session.execute(select(Assignment)).scalars().all()
    assert len(assignments) == 3


def test_seed_assignment_statuses(db):
    """Seeded assignments must cover active and completed statuses."""
    session, _ = db
    statuses = {
        a.status for a in session.execute(select(Assignment)).scalars().all()
    }
    assert "active" in statuses
    assert "completed" in statuses


def test_seed_every_assignment_has_approver(db):
    """Every assignment must have at least one approver."""
    session, _ = db
    assignments = session.execute(select(Assignment)).scalars().all()
    for assignment in assignments:
        session.refresh(assignment)
        assert len(assignment.approvers) >= 1, (
            f"Assignment '{assignment.title}' has no approvers"
        )


def test_seed_payments_assignment_has_two_approvers(db):
    """The 'Payments Regression Suite' assignment has two approvers."""
    session, _ = db
    assignment = session.execute(
        select(Assignment).where(
            Assignment.title == "Payments Regression Suite"
        )
    ).scalar_one()
    session.refresh(assignment)
    assert len(assignment.approvers) == 2


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------

def test_seed_creates_five_access_requests(db):
    session, _ = db
    reqs = session.execute(select(AccessRequest)).scalars().all()
    assert len(reqs) == 5


def test_seed_access_request_statuses_cover_all_scenarios(db):
    """All five status values must be represented in the seed data."""
    session, _ = db
    statuses = {
        r.status
        for r in session.execute(select(AccessRequest)).scalars().all()
    }
    assert statuses == {"pending", "approved", "rejected", "revoked", "expired"}


def test_seed_approved_request_has_resolver(db):
    session, _ = db
    req = session.execute(
        select(AccessRequest).where(AccessRequest.status == "approved")
    ).scalar_one()
    assert req.resolved_by_id is not None
    assert req.resolved_at is not None
    assert req.decision_note is not None


def test_seed_pending_request_has_no_resolver(db):
    session, _ = db
    req = session.execute(
        select(AccessRequest).where(AccessRequest.status == "pending")
    ).scalar_one()
    assert req.resolved_by_id is None
    assert req.resolved_at is None


# ---------------------------------------------------------------------------
# Access-request status history
# ---------------------------------------------------------------------------

def test_seed_every_access_request_has_history(db):
    """Every access request must have at least one status-history row."""
    session, _ = db
    reqs = session.execute(select(AccessRequest)).scalars().all()
    for req in reqs:
        session.refresh(req)
        assert len(req.status_history) >= 1, (
            f"AccessRequest id={req.id} ({req.status}) has no status history"
        )


def test_seed_history_initial_entry_has_no_previous_status(db):
    """The first history entry for each request must have previous_status=None."""
    session, _ = db
    reqs = session.execute(select(AccessRequest)).scalars().all()
    for req in reqs:
        session.refresh(req)
        first = req.status_history[0]
        assert first.previous_status is None, (
            f"First history entry for request id={req.id} should have "
            f"previous_status=None, got '{first.previous_status}'"
        )


def test_seed_revoked_request_has_three_history_entries(db):
    """The revoked request must record pending->approved->revoked transitions."""
    session, _ = db
    req = session.execute(
        select(AccessRequest).where(AccessRequest.status == "revoked")
    ).scalar_one()
    session.refresh(req)
    assert len(req.status_history) == 3
    statuses = [h.status for h in req.status_history]
    assert statuses == ["pending", "approved", "revoked"]


def test_seed_expired_request_system_transition_has_no_actor(db):
    """The system-generated expiry transition must have changed_by_id=None."""
    session, _ = db
    req = session.execute(
        select(AccessRequest).where(AccessRequest.status == "expired")
    ).scalar_one()
    session.refresh(req)
    expiry_entry = next(h for h in req.status_history if h.status == "expired")
    assert expiry_entry.changed_by_id is None


def test_seed_approved_request_has_two_history_entries(db):
    """Approved request must have pending->approved history (two entries)."""
    session, _ = db
    req = session.execute(
        select(AccessRequest).where(AccessRequest.status == "approved")
    ).scalar_one()
    session.refresh(req)
    assert len(req.status_history) == 2
    statuses = [h.status for h in req.status_history]
    assert statuses == ["pending", "approved"]
