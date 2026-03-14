# -*- coding: utf-8 -*-
"""
Modular service layer for evidence management.

Provides helpers to create, query, and export evidence records that are
tied to AccessRequests and/or Assignments.  All writes also append an
AuditLog entry so that evidence operations are fully auditable.

Design principles:
- Every public function accepts an open SQLAlchemy Session as its first
  argument so that callers control transaction boundaries.
- Evidence creation enforces the business rule that at least one of
  ``access_request_id`` or ``assignment_id`` must be supplied.
- Queries return plain lists of model instances; callers decide how to
  serialize or render them.
- Export helpers return plain dicts so they can be serialized to JSON or
  written to CSV by the caller without any further database interaction.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Evidence, AuditLog

# ---------------------------------------------------------------------------
# Valid evidence types (kept in sync with the model docstring)
# ---------------------------------------------------------------------------

VALID_EVIDENCE_TYPES = frozenset(
    {"document", "screenshot", "certificate", "log", "photo", "other"}
)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def add_evidence(
    db: Session,
    *,
    title: str,
    file_path: str,
    uploaded_by_email: str,
    evidence_type: str = "document",
    description: Optional[str] = None,
    uploaded_by_id: Optional[int] = None,
    access_request_id: Optional[int] = None,
    assignment_id: Optional[int] = None,
) -> Evidence:
    """Create and persist a new Evidence record.

    Business rules enforced here (not at the DB level):
    - At least one of ``access_request_id`` or ``assignment_id`` must be
      provided so that every evidence record is traceable.
    - ``evidence_type`` must be one of the recognised values defined in
      ``VALID_EVIDENCE_TYPES``.

    An AuditLog entry is inserted in the same session so that the creation
    event is always recorded alongside the evidence row.

    Args:
        db: An open SQLAlchemy session.
        title: Short human-readable label for this piece of evidence.
        file_path: Relative or absolute path (or URL) to the stored file.
        uploaded_by_email: Email of the actor uploading the evidence,
            captured at call time for long-term auditability.
        evidence_type: One of ``VALID_EVIDENCE_TYPES`` (default "document").
        description: Optional longer context note.
        uploaded_by_id: FK to ``users.id``; nullable for system uploads.
        access_request_id: FK to ``access_requests.id`` (at least one of
            this or ``assignment_id`` is required).
        assignment_id: FK to ``assignments.id`` (at least one of this or
            ``access_request_id`` is required).

    Returns:
        The newly created and flushed ``Evidence`` instance.

    Raises:
        ValueError: If neither ``access_request_id`` nor ``assignment_id``
            is provided, or if ``evidence_type`` is not recognised.
    """
    if access_request_id is None and assignment_id is None:
        raise ValueError(
            "Evidence must be linked to at least one AccessRequest or Assignment. "
            "Provide access_request_id, assignment_id, or both."
        )
    if evidence_type not in VALID_EVIDENCE_TYPES:
        raise ValueError(
            f"Invalid evidence_type {evidence_type!r}. "
            f"Must be one of: {sorted(VALID_EVIDENCE_TYPES)}"
        )

    ev = Evidence(
        title=title,
        file_path=file_path,
        evidence_type=evidence_type,
        description=description,
        uploaded_by_id=uploaded_by_id,
        uploaded_by_email=uploaded_by_email,
        access_request_id=access_request_id,
        assignment_id=assignment_id,
    )
    db.add(ev)
    db.flush()

    # Audit log entry — same pattern as the rest of the codebase.
    parts = []
    if access_request_id is not None:
        parts.append(f"access_request_id={access_request_id}")
    if assignment_id is not None:
        parts.append(f"assignment_id={assignment_id}")
    detail = (
        f"evidence_id={ev.id} type={evidence_type} "
        f"file_path={file_path!r} {' '.join(parts)}"
    )
    db.add(
        AuditLog(
            actor_email=uploaded_by_email,
            action="evidence:uploaded",
            detail=detail,
        )
    )

    return ev


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_evidence_for_request(
    db: Session,
    access_request_id: int,
) -> List[Evidence]:
    """Return all evidence records linked to a given AccessRequest.

    Results are ordered by ``uploaded_at`` ascending so the earliest
    submission appears first (consistent with the status_history ordering
    convention on AccessRequest).

    Args:
        db: An open SQLAlchemy session.
        access_request_id: PK of the target AccessRequest.

    Returns:
        A list of ``Evidence`` instances (may be empty).
    """
    stmt = (
        select(Evidence)
        .where(Evidence.access_request_id == access_request_id)
        .order_by(Evidence.uploaded_at)
    )
    return db.execute(stmt).scalars().all()


def get_evidence_for_assignment(
    db: Session,
    assignment_id: int,
) -> List[Evidence]:
    """Return all evidence records linked to a given Assignment.

    Results are ordered by ``uploaded_at`` ascending.

    Args:
        db: An open SQLAlchemy session.
        assignment_id: PK of the target Assignment.

    Returns:
        A list of ``Evidence`` instances (may be empty).
    """
    stmt = (
        select(Evidence)
        .where(Evidence.assignment_id == assignment_id)
        .order_by(Evidence.uploaded_at)
    )
    return db.execute(stmt).scalars().all()


# ---------------------------------------------------------------------------
# Export helper
# ---------------------------------------------------------------------------


def export_evidence_summary(
    db: Session,
    *,
    access_request_id: Optional[int] = None,
    assignment_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return a serialisable summary of evidence records for export.

    At least one of ``access_request_id`` or ``assignment_id`` must be
    supplied; when both are provided only records that match *all* supplied
    filters are returned (i.e. the filters are ANDed).

    Each dict in the returned list contains the following keys:

    .. code-block:: python

        {
            "id": int,
            "title": str,
            "description": str | None,
            "file_path": str,
            "evidence_type": str,
            "uploaded_at": str,          # ISO-8601 UTC string
            "uploaded_by_email": str,
            "uploaded_by_id": int | None,
            "access_request_id": int | None,
            "assignment_id": int | None,
        }

    Args:
        db: An open SQLAlchemy session.
        access_request_id: Optional filter by AccessRequest PK.
        assignment_id: Optional filter by Assignment PK.

    Returns:
        A list of plain dicts suitable for JSON serialisation or CSV export.

    Raises:
        ValueError: If neither filter is provided.
    """
    if access_request_id is None and assignment_id is None:
        raise ValueError(
            "export_evidence_summary requires at least one of access_request_id or assignment_id."
        )

    stmt = select(Evidence).order_by(Evidence.uploaded_at)
    if access_request_id is not None:
        stmt = stmt.where(Evidence.access_request_id == access_request_id)
    if assignment_id is not None:
        stmt = stmt.where(Evidence.assignment_id == assignment_id)

    results = db.execute(stmt).scalars().all()

    return [
        {
            "id": ev.id,
            "title": ev.title,
            "description": ev.description,
            "file_path": ev.file_path,
            "evidence_type": ev.evidence_type,
            "uploaded_at": ev.uploaded_at.isoformat(),
            "uploaded_by_email": ev.uploaded_by_email,
            "uploaded_by_id": ev.uploaded_by_id,
            "access_request_id": ev.access_request_id,
            "assignment_id": ev.assignment_id,
        }
        for ev in results
    ]
