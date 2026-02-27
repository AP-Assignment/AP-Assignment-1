# -*- coding: utf-8 -*-
"""
Tests for the AccessRequest and AccessRequestStatusHistory data models.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Site, User, AccessRequest, AccessRequestStatusHistory
from app.security import hash_password


@pytest.fixture()
def db():
    """Provide an in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        yield session


@pytest.fixture()
def requester(db):
    u = User(
        name="Alice",
        email="alice@example.com",
        password_hash=hash_password("Password123!"),
        team="Engineering",
        role="user",
        status="active",
        manager_email="boss@example.com",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def approver(db):
    u = User(
        name="Bob",
        email="bob@example.com",
        password_hash=hash_password("Password123!"),
        team="QA Governance",
        role="approver",
        status="active",
        manager_email="director@example.com",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def site(db):
    s = Site(name="Test Hub North", city="Manchester", lat=53.48, lon=-2.24)
    db.add(s)
    db.flush()
    return s


# ---------------------------------------------------------------------------
# AccessRequest model
# ---------------------------------------------------------------------------

def test_access_request_defaults(db, requester):
    req = AccessRequest(requester_id=requester.id, assignment="Payments regression suite")
    db.add(req)
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    assert fetched.status == "pending"
    assert fetched.site_id is None
    assert fetched.resolved_by_id is None
    assert fetched.resolved_at is None
    assert fetched.decision_note is None
    assert fetched.updated_at is None
    assert isinstance(fetched.created_at, datetime)


def test_access_request_with_site(db, requester, site):
    req = AccessRequest(
        requester_id=requester.id,
        site_id=site.id,
        assignment="Networking load tests",
    )
    db.add(req)
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    assert fetched.site_id == site.id
    assert fetched.site.name == "Test Hub North"


def test_access_request_approval_workflow(db, requester, approver, site):
    req = AccessRequest(
        requester_id=requester.id,
        site_id=site.id,
        assignment="Core Platform testing",
    )
    db.add(req)
    db.flush()

    # Simulate approval
    req.status = "approved"
    req.resolved_by_id = approver.id
    req.resolved_at = datetime.utcnow()
    req.decision_note = "Access granted for Q2 sprint."
    req.updated_at = datetime.utcnow()

    db.add(AccessRequestStatusHistory(
        access_request_id=req.id,
        previous_status="pending",
        status="approved",
        changed_by_id=approver.id,
        note="Access granted for Q2 sprint.",
    ))
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    assert fetched.status == "approved"
    assert fetched.resolver.email == "bob@example.com"
    assert fetched.decision_note == "Access granted for Q2 sprint."


def test_access_request_rejection(db, requester, approver):
    req = AccessRequest(requester_id=requester.id, assignment="Data Pipelines access")
    db.add(req)
    db.flush()

    req.status = "rejected"
    req.resolved_by_id = approver.id
    req.resolved_at = datetime.utcnow()
    req.decision_note = "Insufficient justification."
    req.updated_at = datetime.utcnow()

    db.add(AccessRequestStatusHistory(
        access_request_id=req.id,
        previous_status="pending",
        status="rejected",
        changed_by_id=approver.id,
        note="Insufficient justification.",
    ))
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    assert fetched.status == "rejected"
    assert len(fetched.status_history) == 1


# ---------------------------------------------------------------------------
# AccessRequestStatusHistory model
# ---------------------------------------------------------------------------

def test_status_history_records_transitions(db, requester, approver):
    req = AccessRequest(requester_id=requester.id, assignment="Devices testing")
    db.add(req)
    db.flush()

    # Record initial "pending" state (no previous status on creation)
    db.add(AccessRequestStatusHistory(
        access_request_id=req.id,
        previous_status=None,
        status="pending",
        changed_by_id=requester.id,
        note="Request submitted.",
    ))
    db.flush()

    # Simulate approver approving
    req.status = "approved"
    req.resolved_by_id = approver.id
    req.resolved_at = datetime.utcnow()
    req.updated_at = datetime.utcnow()
    db.add(AccessRequestStatusHistory(
        access_request_id=req.id,
        previous_status="pending",
        status="approved",
        changed_by_id=approver.id,
    ))
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    assert len(fetched.status_history) == 2
    statuses = [h.status for h in fetched.status_history]
    assert statuses == ["pending", "approved"]
    previous_statuses = [h.previous_status for h in fetched.status_history]
    assert previous_statuses == [None, "pending"]


def test_status_history_without_actor(db, requester):
    """changed_by_id is optional to allow system-generated transitions."""
    req = AccessRequest(requester_id=requester.id, assignment="Auto-expiry test")
    db.add(req)
    db.flush()

    db.add(AccessRequestStatusHistory(
        access_request_id=req.id,
        previous_status="approved",
        status="revoked",
        changed_by_id=None,
        note="Automatically revoked after 90 days.",
    ))
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    history = fetched.status_history[0]
    assert history.changed_by_id is None
    assert history.previous_status == "approved"
    assert history.note == "Automatically revoked after 90 days."


def test_status_history_previous_status_is_none_for_initial_entry(db, requester):
    """The first history entry has no previous status (request was just created)."""
    req = AccessRequest(requester_id=requester.id, assignment="Initial entry test")
    db.add(req)
    db.flush()

    db.add(AccessRequestStatusHistory(
        access_request_id=req.id,
        previous_status=None,
        status="pending",
        changed_by_id=requester.id,
        note="Access request created.",
    ))
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    history = fetched.status_history[0]
    assert history.previous_status is None
    assert history.status == "pending"


def test_status_history_full_lifecycle(db, requester, approver):
    """Validate previous/next status through a full pending→approved→revoked cycle."""
    req = AccessRequest(requester_id=requester.id, assignment="Full lifecycle test")
    db.add(req)
    db.flush()

    transitions = [
        (None, "pending"),
        ("pending", "approved"),
        ("approved", "revoked"),
    ]
    for prev, nxt in transitions:
        db.add(AccessRequestStatusHistory(
            access_request_id=req.id,
            previous_status=prev,
            status=nxt,
            changed_by_id=approver.id if prev else requester.id,
        ))
    db.commit()

    fetched = db.get(AccessRequest, req.id)
    history = fetched.status_history
    assert len(history) == 3
    assert [(h.previous_status, h.status) for h in history] == transitions


# ---------------------------------------------------------------------------
# Relationship back-references
# ---------------------------------------------------------------------------

def test_user_access_requests_backref(db, requester, approver):
    req1 = AccessRequest(requester_id=requester.id, assignment="Assignment A")
    req2 = AccessRequest(requester_id=requester.id, assignment="Assignment B")
    db.add_all([req1, req2])
    db.flush()

    req1.status = "approved"
    req1.resolved_by_id = approver.id
    req1.resolved_at = datetime.utcnow()
    req1.updated_at = datetime.utcnow()
    db.commit()

    db.refresh(requester)
    db.refresh(approver)
    assert len(requester.access_requests) == 2
    assert len(approver.resolved_access_requests) == 1


def test_site_access_requests_backref(db, requester, site):
    req = AccessRequest(requester_id=requester.id, site_id=site.id, assignment="Site-specific task")
    db.add(req)
    db.commit()

    db.refresh(site)
    assert len(site.access_requests) == 1
    assert site.access_requests[0].assignment == "Site-specific task"
