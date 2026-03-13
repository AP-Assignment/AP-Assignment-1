# -*- coding: utf-8 -*-
"""
Tests for the Evidence data model and evidence service.

Covers:
- Evidence model defaults and field storage
- Evidence linked to an AccessRequest
- Evidence linked to an Assignment
- Evidence linked to both (cross-linked)
- Relationship back-references (access_request.evidence, assignment.evidence,
  user.uploaded_evidence)
- Cascade delete: removing a parent also removes its evidence rows
- Service layer: add_evidence (success and validation errors)
- Service layer: get_evidence_for_request / get_evidence_for_assignment
- Service layer: export_evidence_summary
- Audit log entry created by add_evidence
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    User,
    Site,
    Assignment,
    AccessRequest,
    Evidence,
    AuditLog,
)
from app.security import hash_password
from app.services.evidence import (
    VALID_EVIDENCE_TYPES,
    add_evidence,
    get_evidence_for_request,
    get_evidence_for_assignment,
    export_evidence_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """Provide an in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        yield session


@pytest.fixture()
def uploader(db):
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
def site(db):
    s = Site(name="Test Hub North", city="Manchester", lat=53.48, lon=-2.24)
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def assignment(db, uploader):
    a = Assignment(title="Payments Regression Suite", owner_id=uploader.id)
    db.add(a)
    db.flush()
    return a


@pytest.fixture()
def access_request(db, uploader):
    req = AccessRequest(requester_id=uploader.id, assignment="Payments Regression Suite")
    db.add(req)
    db.flush()
    return req


# ---------------------------------------------------------------------------
# Evidence model — direct construction
# ---------------------------------------------------------------------------


def test_evidence_defaults(db, uploader, access_request):
    ev = Evidence(
        title="Approval screenshot",
        file_path="/uploads/evidence/req_1_approval.png",
        uploaded_by_email=uploader.email,
        uploaded_by_id=uploader.id,
        access_request_id=access_request.id,
    )
    db.add(ev)
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.evidence_type == "document"
    assert fetched.description is None
    assert isinstance(fetched.uploaded_at, datetime)
    assert fetched.uploaded_by_email == "alice@example.com"
    assert fetched.assignment_id is None


def test_evidence_all_fields(db, uploader, access_request, assignment):
    ev = Evidence(
        title="Compliance certificate",
        description="ISO 27001 certificate valid through 2026.",
        file_path="/uploads/evidence/cert_2026.pdf",
        evidence_type="certificate",
        uploaded_by_email=uploader.email,
        uploaded_by_id=uploader.id,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    db.add(ev)
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.title == "Compliance certificate"
    assert "ISO 27001" in fetched.description
    assert fetched.evidence_type == "certificate"
    assert fetched.access_request_id == access_request.id
    assert fetched.assignment_id == assignment.id


def test_evidence_system_upload_no_user(db, access_request):
    """Evidence uploaded by a system process has no user FK."""
    ev = Evidence(
        title="Automated log export",
        file_path="/uploads/evidence/auto_log.txt",
        evidence_type="log",
        uploaded_by_email="system@scheduler",
        uploaded_by_id=None,
        access_request_id=access_request.id,
    )
    db.add(ev)
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.uploaded_by_id is None
    assert fetched.uploaded_by_email == "system@scheduler"
    assert fetched.uploaded_by is None


# ---------------------------------------------------------------------------
# Evidence model — relationships
# ---------------------------------------------------------------------------


def test_evidence_uploaded_by_relationship(db, uploader, access_request):
    ev = Evidence(
        title="Photo",
        file_path="/uploads/evidence/photo.jpg",
        evidence_type="photo",
        uploaded_by_email=uploader.email,
        uploaded_by_id=uploader.id,
        access_request_id=access_request.id,
    )
    db.add(ev)
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.uploaded_by.email == "alice@example.com"


def test_evidence_access_request_relationship(db, uploader, access_request):
    ev = Evidence(
        title="Supporting doc",
        file_path="/uploads/evidence/doc.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.add(ev)
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.access_request_ref.id == access_request.id


def test_evidence_assignment_relationship(db, uploader, assignment):
    ev = Evidence(
        title="Assignment doc",
        file_path="/uploads/evidence/assignment_doc.pdf",
        uploaded_by_email=uploader.email,
        assignment_id=assignment.id,
    )
    db.add(ev)
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.assignment_ref.title == "Payments Regression Suite"


def test_access_request_evidence_backref(db, uploader, access_request):
    ev1 = Evidence(
        title="Doc 1",
        file_path="/uploads/ev1.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    ev2 = Evidence(
        title="Doc 2",
        file_path="/uploads/ev2.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.add_all([ev1, ev2])
    db.commit()

    db.refresh(access_request)
    assert len(access_request.evidence) == 2


def test_assignment_evidence_backref(db, uploader, assignment):
    ev = Evidence(
        title="Assignment evidence",
        file_path="/uploads/assign_ev.pdf",
        uploaded_by_email=uploader.email,
        assignment_id=assignment.id,
    )
    db.add(ev)
    db.commit()

    db.refresh(assignment)
    assert len(assignment.evidence) == 1
    assert assignment.evidence[0].title == "Assignment evidence"


def test_user_uploaded_evidence_backref(db, uploader, access_request):
    ev1 = Evidence(
        title="Ev A",
        file_path="/uploads/eva.pdf",
        uploaded_by_email=uploader.email,
        uploaded_by_id=uploader.id,
        access_request_id=access_request.id,
    )
    ev2 = Evidence(
        title="Ev B",
        file_path="/uploads/evb.pdf",
        uploaded_by_email=uploader.email,
        uploaded_by_id=uploader.id,
        access_request_id=access_request.id,
    )
    db.add_all([ev1, ev2])
    db.commit()

    db.refresh(uploader)
    assert len(uploader.uploaded_evidence) == 2


# ---------------------------------------------------------------------------
# Cascade behaviour (no delete-orphan)
# ---------------------------------------------------------------------------


def test_removing_evidence_from_collection_does_not_delete_it(
    db, uploader, access_request, assignment
):
    """With cascade="all" (no delete-orphan), removing an Evidence row from a
    parent's collection does NOT delete the row from the database.  This is
    the key safety property for cross-linked Evidence: clearing an
    AccessRequest's evidence list should not destroy a record that is still
    relevant to an Assignment."""
    ev = Evidence(
        title="Cross-linked evidence",
        file_path="/uploads/cross.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    db.add(ev)
    db.commit()
    ev_id = ev.id

    # Remove from the AccessRequest's collection (simulates unlinking, not deletion)
    db.refresh(access_request)
    access_request.evidence.remove(ev)
    db.commit()

    # Evidence row must still exist because we only unlinked it, not deleted it
    surviving = db.get(Evidence, ev_id)
    assert surviving is not None
    assert surviving.assignment_id == assignment.id


def test_deleting_access_request_cascades_for_cross_linked_evidence(
    db, uploader, access_request, assignment
):
    """Deleting one parent of a cross-linked Evidence row should cascade and delete
    the Evidence row, even if it is also linked to another parent.

    This locks in the expected behavior for cross-linked Evidence when a parent
    object is actually deleted (as opposed to merely being unlinked from a
    relationship collection)."""
    ev = Evidence(
        title="Cross-linked evidence for cascade delete",
        file_path="/uploads/cross-cascade.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    db.add(ev)
    db.commit()
    ev_id = ev.id

    # Delete the AccessRequest; cascade rules should remove the Evidence row
    db.delete(access_request)
    db.commit()

    # Evidence row must no longer exist after deleting one of its parents
    assert db.get(Evidence, ev_id) is None


def test_removing_evidence_from_assignment_collection_does_not_delete_it(
    db, uploader, access_request, assignment
):
    """Symmetric: removing Evidence from Assignment.evidence collection does not
    delete the row — it remains accessible via the AccessRequest link."""
    ev = Evidence(
        title="Cross-linked evidence",
        file_path="/uploads/cross.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    db.add(ev)
    db.commit()
    ev_id = ev.id

    db.refresh(assignment)
    assignment.evidence.remove(ev)
    db.commit()

    surviving = db.get(Evidence, ev_id)
    assert surviving is not None
    assert surviving.access_request_id == access_request.id


# ---------------------------------------------------------------------------
# Service: add_evidence
# ---------------------------------------------------------------------------


def test_add_evidence_linked_to_request(db, uploader, access_request):
    ev = add_evidence(
        db,
        title="Request screenshot",
        file_path="/uploads/req_screen.png",
        uploaded_by_email=uploader.email,
        evidence_type="screenshot",
        uploaded_by_id=uploader.id,
        access_request_id=access_request.id,
    )
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.title == "Request screenshot"
    assert fetched.evidence_type == "screenshot"
    assert fetched.access_request_id == access_request.id
    assert fetched.assignment_id is None


def test_add_evidence_linked_to_assignment(db, uploader, assignment):
    ev = add_evidence(
        db,
        title="Assignment certificate",
        file_path="/uploads/assign_cert.pdf",
        uploaded_by_email=uploader.email,
        evidence_type="certificate",
        assignment_id=assignment.id,
    )
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.assignment_id == assignment.id
    assert fetched.access_request_id is None


def test_add_evidence_linked_to_both(db, uploader, access_request, assignment):
    ev = add_evidence(
        db,
        title="Cross-linked evidence",
        file_path="/uploads/cross.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    db.commit()

    fetched = db.get(Evidence, ev.id)
    assert fetched.access_request_id == access_request.id
    assert fetched.assignment_id == assignment.id


def test_add_evidence_raises_if_no_parent(db, uploader):
    """Service must reject evidence not tied to any request or assignment."""
    with pytest.raises(ValueError, match="at least one AccessRequest or Assignment"):
        add_evidence(
            db,
            title="Orphan evidence",
            file_path="/uploads/orphan.pdf",
            uploaded_by_email=uploader.email,
        )


def test_add_evidence_raises_on_invalid_type(db, uploader, access_request):
    with pytest.raises(ValueError, match="Invalid evidence_type"):
        add_evidence(
            db,
            title="Bad type",
            file_path="/uploads/bad.pdf",
            uploaded_by_email=uploader.email,
            evidence_type="video",  # not in VALID_EVIDENCE_TYPES
            access_request_id=access_request.id,
        )


def test_add_evidence_creates_audit_log(db, uploader, access_request):
    add_evidence(
        db,
        title="Audited evidence",
        file_path="/uploads/audited.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.commit()

    logs = db.query(AuditLog).filter(AuditLog.action == "evidence:uploaded").all()
    assert len(logs) == 1
    assert logs[0].actor_email == uploader.email
    assert "access_request_id=" in logs[0].detail


def test_add_evidence_default_type_is_document(db, uploader, access_request):
    ev = add_evidence(
        db,
        title="Default type",
        file_path="/uploads/default.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.commit()
    assert ev.evidence_type == "document"


# ---------------------------------------------------------------------------
# Service: VALID_EVIDENCE_TYPES constant
# ---------------------------------------------------------------------------


def test_valid_evidence_types_contains_expected_values():
    assert "document" in VALID_EVIDENCE_TYPES
    assert "screenshot" in VALID_EVIDENCE_TYPES
    assert "certificate" in VALID_EVIDENCE_TYPES
    assert "log" in VALID_EVIDENCE_TYPES
    assert "photo" in VALID_EVIDENCE_TYPES
    assert "other" in VALID_EVIDENCE_TYPES


# ---------------------------------------------------------------------------
# Service: get_evidence_for_request
# ---------------------------------------------------------------------------


def test_get_evidence_for_request_returns_correct_records(db, uploader, access_request):
    ev1 = add_evidence(
        db,
        title="First",
        file_path="/uploads/first.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    ev2 = add_evidence(
        db,
        title="Second",
        file_path="/uploads/second.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.commit()

    results = get_evidence_for_request(db, access_request.id)
    assert len(results) == 2
    ids = {r.id for r in results}
    assert ev1.id in ids
    assert ev2.id in ids


def test_get_evidence_for_request_empty(db, access_request):
    results = get_evidence_for_request(db, access_request.id)
    assert results == []


def test_get_evidence_for_request_only_returns_matching(db, uploader):
    """Evidence for a different request is not returned."""
    req1 = AccessRequest(requester_id=uploader.id, assignment="Task A")
    req2 = AccessRequest(requester_id=uploader.id, assignment="Task B")
    db.add_all([req1, req2])
    db.flush()

    add_evidence(
        db,
        title="For req1",
        file_path="/uploads/req1.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=req1.id,
    )
    db.commit()

    results = get_evidence_for_request(db, req2.id)
    assert results == []


# ---------------------------------------------------------------------------
# Service: get_evidence_for_assignment
# ---------------------------------------------------------------------------


def test_get_evidence_for_assignment_returns_correct_records(db, uploader, assignment):
    ev = add_evidence(
        db,
        title="Assignment ev",
        file_path="/uploads/assign_ev.pdf",
        uploaded_by_email=uploader.email,
        assignment_id=assignment.id,
    )
    db.commit()

    results = get_evidence_for_assignment(db, assignment.id)
    assert len(results) == 1
    assert results[0].id == ev.id


def test_get_evidence_for_assignment_empty(db, assignment):
    assert get_evidence_for_assignment(db, assignment.id) == []


# ---------------------------------------------------------------------------
# Service: export_evidence_summary
# ---------------------------------------------------------------------------


def test_export_evidence_summary_for_request(db, uploader, access_request):
    add_evidence(
        db,
        title="Export test",
        file_path="/uploads/export.pdf",
        uploaded_by_email=uploader.email,
        evidence_type="document",
        description="A description.",
        access_request_id=access_request.id,
    )
    db.commit()

    summary = export_evidence_summary(db, access_request_id=access_request.id)
    assert len(summary) == 1
    record = summary[0]
    assert record["title"] == "Export test"
    assert record["file_path"] == "/uploads/export.pdf"
    assert record["evidence_type"] == "document"
    assert record["description"] == "A description."
    assert record["uploaded_by_email"] == uploader.email
    assert record["access_request_id"] == access_request.id
    assert "T" in record["uploaded_at"]  # ISO-8601 datetime contains "T"


def test_export_evidence_summary_for_assignment(db, uploader, assignment):
    add_evidence(
        db,
        title="Assignment export",
        file_path="/uploads/assign_export.pdf",
        uploaded_by_email=uploader.email,
        assignment_id=assignment.id,
    )
    db.commit()

    summary = export_evidence_summary(db, assignment_id=assignment.id)
    assert len(summary) == 1
    assert summary[0]["assignment_id"] == assignment.id


def test_export_evidence_summary_both_filters(db, uploader, access_request, assignment):
    """When both filters are supplied, only cross-linked records are returned."""
    # Evidence linked to both
    add_evidence(
        db,
        title="Cross-linked",
        file_path="/uploads/cross.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    # Evidence linked to request only — should NOT appear
    add_evidence(
        db,
        title="Request only",
        file_path="/uploads/req_only.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.commit()

    summary = export_evidence_summary(
        db,
        access_request_id=access_request.id,
        assignment_id=assignment.id,
    )
    assert len(summary) == 1
    assert summary[0]["title"] == "Cross-linked"


def test_export_evidence_summary_raises_if_no_filter(db):
    with pytest.raises(ValueError, match="at least one of"):
        export_evidence_summary(db)


def test_export_evidence_summary_serializable_fields(db, uploader, access_request):
    """All returned fields must be JSON-serializable primitives."""
    import json

    add_evidence(
        db,
        title="Serializable",
        file_path="/uploads/serial.pdf",
        uploaded_by_email=uploader.email,
        access_request_id=access_request.id,
    )
    db.commit()

    summary = export_evidence_summary(db, access_request_id=access_request.id)
    # Should not raise
    json.dumps(summary)
