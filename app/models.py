# -*- coding: utf-8 -*-
"""
Data model for the AP Assignment booking system.

Relationship map (→ = FK / many-to-one, ↔ = bidirectional):
  Site          1 ↔ N  Location          (site.locations / location.site)
  Site          1 ↔ N  Machine           (site.machines / machine.site)
  Site          1 ↔ N  AccessRequest     (site.access_requests / access_request.site)
  Location      1 ↔ N  Location          self-referential hierarchy (parent/children)
  Location      1 ↔ N  Machine           (location.machines / machine.location) [optional]
  User          1 ↔ N  BookingRequest    as requester (user.requests / booking.requester)
  User          1 ↔ N  BookingRequest    as approver  (user.approvals / booking.approver)
  User          1 ↔ N  Notification      (user.notifications / notification.user)
  User          1 ↔ N  AccessRequest     as requester (user.access_requests / access_request.requester)
  User          1 ↔ N  AccessRequest     as resolver  (user.resolved_access_requests / access_request.resolver)
  User          1 ↔ N  Assignment        as owner     (user.owned_assignments / assignment.owner)
  User          1 ↔ N  AssignmentApprover (user.assignment_approver_roles / assignment_approver.approver)
  User          1 ↔ N  Evidence          as uploader  (user.uploaded_evidence / evidence.uploaded_by) [optional]
  BookingRequest 1 ↔ N  BookingItem      (booking.items / booking_item.booking)
  BookingRequest 1 ↔ 1  AccessRequest    (booking.access_request / access_request.booking_request)
  Machine        1 ↔ N  BookingItem      (machine.booking_items / booking_item.machine)
  Assignment     1 ↔ N  AssignmentApprover (assignment.approvers / assignment_approver.assignment)
  Assignment     1 ↔ N  AccessRequest    (assignment.access_requests / access_request.assignment_ref)
  Assignment     1 ↔ N  Evidence         (assignment.evidence / evidence.assignment_ref) [optional]
  AccessRequest  1 ↔ N  AccessRequestStatusHistory (access_request.status_history)
  AccessRequest  1 ↔ N  Evidence         (access_request.evidence / evidence.access_request_ref) [optional]

  Edge cases:
  - Evidence.access_request_id and Evidence.assignment_id are both nullable, but
    application logic (enforced in the service layer) requires at least one to be
    set so that every evidence record is traceable to a request or assignment.
  - Evidence.uploaded_by_email is denormalized (captured at upload time) so that
    audit records remain accurate even after a user account is deleted or renamed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from flask_login import UserMixin
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

import pyotp


class Site(Base):
    """A physical testing site (e.g. a city hub).

    One site contains many Locations and many Machines.  AccessRequests are
    scoped to a site so that approvers know where physical access is needed.
    """

    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    # Short unique code for the site, e.g. "MAN", "LON" (optional, for future use)
    code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, unique=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lat: Mapped[float] = mapped_column(nullable=False)
    lon: Mapped[float] = mapped_column(nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    # Cascade delete-orphan: removing a site removes all its locations
    locations: Mapped[List["Location"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    machines: Mapped[List["Machine"]] = relationship(back_populates="site")
    access_requests: Mapped[List["AccessRequest"]] = relationship(back_populates="site")


class Location(Base):
    """A named area within a Site (e.g. building, floor, room).

    Supports an arbitrary hierarchy through the self-referential ``parent_id``
    foreign key, allowing structures such as::

        Site → Building → Floor → Room

    Normalization notes:
    - The unique constraint on (site_id, code) ensures codes are unique within
      a site but the same code may be reused across different sites (3NF).
    - Hierarchical nesting is handled via the self-referential parent_id FK
      rather than storing path/depth columns (avoids update anomalies).
    - cascade="all, delete-orphan" on children ensures that deleting a parent
      location also removes all its nested children, preventing orphan rows.

    The ``metadata_json`` column stores an optional JSON string for any
    additional key/value pairs needed by future extensions.
    """

    __tablename__ = "locations"
    __table_args__ = (
        # A location code must be unique within a site; the same code is
        # allowed in different sites (e.g. both Manchester and London can
        # have a "LAB" area).
        UniqueConstraint("site_id", "code", name="uq_location_site_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Short code unique within the site, e.g. "B1-F2-R03"
    code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # Every location belongs to exactly one site (denormalized from the parent
    # chain for fast site-scoped queries without recursive joins).
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    # Optional parent for hierarchical nesting (e.g. floor inside a building)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    floor: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Arbitrary JSON string for extensible metadata
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    site: Mapped["Site"] = relationship(back_populates="locations")
    parent: Mapped[Optional["Location"]] = relationship(
        back_populates="children", remote_side="Location.id"
    )
    # cascade="all, delete-orphan" so that removing a parent location also
    # removes all nested child locations, preventing orphan rows.
    children: Mapped[List["Location"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    # Machines physically placed in this location (optional FK on Machine side)
    machines: Mapped[List["Machine"]] = relationship(back_populates="location")


class Machine(Base):
    """A physical or virtual test machine at a site.

    Normalization notes:
    - site_id (NOT NULL) links every machine to its site — the primary
      grouping for booking and access-control purposes.
    - location_id (nullable) optionally places the machine in a specific room
      or area within the site, enabling finer-grained inventory management
      without requiring every machine to have a location record.
    - machine_type and category are stored as constrained string values rather
      than FK references to lookup tables; this is acceptable for a small,
      stable enumeration (lab | virtual) where a full reference table would add
      join complexity with no normalisation benefit.
    """

    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    machine_type: Mapped[str] = mapped_column(String(20), nullable=False)  # lab | virtual
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="available")  # available | out_of_service
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    # Optional finer-grained placement within the site (e.g. Building A, Floor 2)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)

    # Relationships
    site: Mapped["Site"] = relationship(back_populates="machines")
    location: Mapped[Optional["Location"]] = relationship(back_populates="machines")
    booking_items: Mapped[List["BookingItem"]] = relationship(back_populates="machine")


class User(Base, UserMixin):
    """An authenticated system user.

    Normalization notes:
    - manager_email is stored as a plain string rather than a FK to another
      User row because managers may not be system users themselves; storing a
      bare email avoids a nullable self-referential FK that would complicate
      registration flows.
    - role and status are stable, small enumerations stored as strings rather
      than FK references to lookup tables (acceptable for this domain size).
    """

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    team: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")  # user | approver | admin
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | active | rejected
    manager_email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    two_fa_secret: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    two_fa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships — explicit foreign_keys disambiguate the two FKs on
    # BookingRequest and AccessRequest that both point back to users.id
    requests: Mapped[List["BookingRequest"]] = relationship(
        back_populates="requester", foreign_keys="BookingRequest.requester_id"
    )
    approvals: Mapped[List["BookingRequest"]] = relationship(
        back_populates="approver", foreign_keys="BookingRequest.approver_id"
    )
    notifications: Mapped[List["Notification"]] = relationship(back_populates="user")
    access_requests: Mapped[List["AccessRequest"]] = relationship(
        back_populates="requester", foreign_keys="AccessRequest.requester_id"
    )
    resolved_access_requests: Mapped[List["AccessRequest"]] = relationship(
        back_populates="resolver", foreign_keys="AccessRequest.resolved_by_id"
    )
    owned_assignments: Mapped[List["Assignment"]] = relationship(
        back_populates="owner", foreign_keys="Assignment.owner_id"
    )
    assignment_approver_roles: Mapped[List["AssignmentApprover"]] = relationship(
        back_populates="approver", foreign_keys="AssignmentApprover.approver_id"
    )
    uploaded_evidence: Mapped[List["Evidence"]] = relationship(
        back_populates="uploaded_by", foreign_keys="Evidence.uploaded_by_id"
    )

    def is_active(self) -> bool:
        return self.status == "active"

    def generate_two_fa_secret(self) -> str:
        """Generate and store a new 2FA secret."""
        secret = pyotp.random_base32()
        self.two_fa_secret = secret
        return secret

    def get_totp(self) -> pyotp.TOTP:
        """Get TOTP object for this user."""
        if not self.two_fa_secret:
            raise ValueError("2FA not set up for this user")
        return pyotp.TOTP(self.two_fa_secret)

    def verify_totp(self, token: str) -> bool:
        """Verify a TOTP token.
        
        Strips whitespace from token and allows small clock drift (±1 window).
        """
        if not self.two_fa_secret:
            return False
        try:
            token = token.strip()
            return self.get_totp().verify(token, valid_window=1)
        except (AttributeError, ValueError, TypeError):
            return False

    def get_provisioning_uri(self) -> str:
        """Get provisioning URI for QR code generation."""
        if not self.two_fa_secret:
            raise ValueError("2FA secret not set")
        return self.get_totp().provisioning_uri(name=self.email, issuer_name="AP Assignment System")


class BookingRequest(Base):
    """A user's request to use one or more machines for a time window.

    Normalization notes:
    - requester_id and approver_id are separate FK columns on the same table
      (users.id); explicit foreign_keys declarations on each relationship
      prevent SQLAlchemy ambiguity errors.
    - Approval metadata (approver_id, decision_note, decided_at) is kept on
      this table rather than in a separate approval table because a booking
      has at most one decision; a separate table would add join complexity
      with no normalisation benefit for a 1:1 optional relationship.
    - checked_in and no_show are boolean flags updated by the no-show service;
      keeping them here avoids a separate attendance table for a simple flag.
    """

    __tablename__ = "booking_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | approved | rejected | cancelled | expired
    approver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # created_at is used by SLA automation to compute request age.
    # NOTE: If upgrading an existing SQLite app.db, recreate the database or add
    # this column manually: ALTER TABLE booking_requests ADD COLUMN created_at DATETIME;
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    checked_in: Mapped[bool] = mapped_column(Boolean, default=False)
    no_show: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    requester: Mapped["User"] = relationship(back_populates="requests", foreign_keys=[requester_id])
    approver: Mapped[Optional["User"]] = relationship(back_populates="approvals", foreign_keys=[approver_id])
    # cascade="all, delete-orphan" so that cancelling/deleting a booking also
    # removes its line items (BookingItem rows) atomically.
    items: Mapped[List["BookingItem"]] = relationship(back_populates="booking", cascade="all, delete-orphan")
    # 1:1 optional link to an AccessRequest created from this booking.
    access_request: Mapped[Optional["AccessRequest"]] = relationship(
        back_populates="booking_request", uselist=False
    )


class BookingItem(Base):
    """A single machine line-item within a BookingRequest.

    Normalization notes:
    - This is a pure join/line-item table (BookingRequest ↔ Machine M:N).
    - The unique constraint on (booking_id, machine_id) enforces that the same
      machine cannot appear more than once in a single booking, preventing
      duplicate line items and the data redundancy they would cause.
    """

    __tablename__ = "booking_items"
    __table_args__ = (
        # Prevent the same machine being booked twice within one request.
        UniqueConstraint("booking_id", "machine_id", name="uq_booking_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking_requests.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)

    # Relationships
    booking: Mapped["BookingRequest"] = relationship(back_populates="items")
    machine: Mapped["Machine"] = relationship(back_populates="booking_items")


class Notification(Base):
    """An in-app notification delivered to a user.

    Normalization notes:
    - sent_at is nullable; NULL means the notification has not yet been
      dispatched, allowing the notification service to query unsent rows
      efficiently without a separate queue table.
    """

    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="notifications")


class AuditLog(Base):
    """Immutable append-only log of significant system actions.

    Normalization notes:
    - actor_email is stored as a denormalized string (rather than a FK to
      users.id) intentionally: audit records must remain accurate even after
      a user account is deleted or their email is changed, so capturing the
      email at the time of the action is the correct auditing pattern.
    - This table should never be updated or deleted from application code;
      only INSERTs are expected.
    """

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(String(700), nullable=False)


class AccessRequest(Base):
    """A request by a user to be granted access to a site for a specific assignment.

    Normalization notes:
    - ``assignment`` (String) is a free-text description field kept for
      backward compatibility and for cases where no formal Assignment record
      exists.  When ``assignment_id`` is set, this field acts as a human-
      readable label (the assignment title) captured at request time so that
      the description remains readable even if the Assignment record changes.
    - ``site_id`` is nullable because some requests are global rather than
      tied to a specific site.
    - ``assignment_id`` is nullable for backward compatibility with requests
      created before the Assignment model was introduced.
    - ``booking_request_id`` is nullable for access requests not originating
      from a booking. The unique constraint enforces at most one AccessRequest
      per BookingRequest (1:1 link).
    - Approval metadata (resolved_by_id, resolved_at, decision_note) follows
      the same pattern as BookingRequest: a single optional decision captured
      inline to avoid a separate 1:1 table.
    - Status history is tracked in the separate AccessRequestStatusHistory
      table (immutable audit trail) rather than in this table, keeping this
      table lean and allowing full transition history to be reconstructed.
    """

    __tablename__ = "access_requests"
    __table_args__ = (
        # Enforce at most one AccessRequest per BookingRequest (1:1 link).
        UniqueConstraint("booking_request_id", name="uq_access_request_booking"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    site_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sites.id"), nullable=True)
    assignment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    # FK to the BookingRequest that triggered this access request (1:1, optional).
    booking_request_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("booking_requests.id"), nullable=True
    )
    # Human-readable description of the assignment/project; captured at
    # request time for readability independent of the Assignment record.
    assignment: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | approved | rejected | revoked | expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)

    # Relationships
    requester: Mapped["User"] = relationship(
        back_populates="access_requests", foreign_keys=[requester_id]
    )
    resolver: Mapped[Optional["User"]] = relationship(
        back_populates="resolved_access_requests", foreign_keys=[resolved_by_id]
    )
    site: Mapped[Optional["Site"]] = relationship(back_populates="access_requests")
    assignment_ref: Mapped[Optional["Assignment"]] = relationship(
        back_populates="access_requests", foreign_keys=[assignment_id]
    )
    booking_request: Mapped[Optional["BookingRequest"]] = relationship(
        back_populates="access_request", foreign_keys=[booking_request_id]
    )
    # Ordered by changed_at so that history[0] is always the earliest entry.
    # cascade="all, delete-orphan" keeps history in sync with the request.
    status_history: Mapped[List["AccessRequestStatusHistory"]] = relationship(
        back_populates="access_request", cascade="all, delete-orphan", order_by="AccessRequestStatusHistory.changed_at"
    )
    # Evidence can also be linked to an Assignment, so we avoid delete-orphan here
    # to prevent deleting shared Evidence when an AccessRequest is removed.
    # Orphan cleanup must be handled explicitly at the application/service layer.
    evidence: Mapped[List["Evidence"]] = relationship(
        back_populates="access_request_ref",
        foreign_keys="Evidence.access_request_id",
        cascade="save-update, merge",
    )


class AccessRequestStatusHistory(Base):
    """Immutable audit trail of every status transition on an AccessRequest.

    Each row captures a single status change, recording both the previous and
    next status so that the full transition history can be reconstructed for
    compliance and audit purposes.

    Normalization notes:
    - previous_status is denormalized (copied from the current AccessRequest
      status at transition time) to make the history self-contained; querying
      the full transition chain does not require joining back to the parent row.
    - changed_by_id is nullable to support system-generated transitions (e.g.
      automatic revocation after an expiry period).
    - This table is append-only; rows are never updated after insertion.
    """

    __tablename__ = "access_request_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_request_id: Mapped[int] = mapped_column(ForeignKey("access_requests.id"), nullable=False)
    # Status value *before* this transition (None for the initial creation entry)
    previous_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Status value *after* this transition
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)

    access_request: Mapped["AccessRequest"] = relationship(back_populates="status_history")
    changed_by: Mapped[Optional["User"]] = relationship(foreign_keys=[changed_by_id])


class Assignment(Base):
    """A formal assignment or project that users are working on, requiring site access.

    Normalization notes:
    - owner_id FK links every assignment to exactly one responsible user.
    - Approvers are stored in the separate AssignmentApprover join table
      (M:N between assignments and users) rather than as a comma-separated
      list or repeated columns, satisfying 1NF and enabling independent
      querying of approver roles.
    - AccessRequests reference this table via an optional FK (assignment_id)
      so that multiple access requests can be grouped under one assignment
      without duplicating the title or description on each request.
    """

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active | completed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    owner: Mapped["User"] = relationship(back_populates="owned_assignments", foreign_keys=[owner_id])
    # cascade="all, delete-orphan" so that removing an assignment also removes
    # its approver mappings (preventing stale AssignmentApprover rows).
    approvers: Mapped[List["AssignmentApprover"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )
    access_requests: Mapped[List["AccessRequest"]] = relationship(
        back_populates="assignment_ref", foreign_keys="AccessRequest.assignment_id"
    )
    # Evidence can also be linked to an AccessRequest, so we restrict the cascade
    # to save-update and merge only — no delete propagation.  Orphan cleanup must
    # be handled explicitly at the application/service layer.
    evidence: Mapped[List["Evidence"]] = relationship(
        back_populates="assignment_ref",
        foreign_keys="Evidence.assignment_id",
        cascade="save-update, merge",
    )


class AssignmentApprover(Base):
    """Records which users are designated approvers for a given assignment.

    Normalization notes:
    - This is a join table implementing the M:N relationship between
      Assignment and User (in the approver role).
    - The unique constraint on (assignment_id, approver_id) prevents the same
      user being added as an approver more than once for the same assignment,
      which would create redundant rows with no semantic difference.
    - assigned_at records when the approver role was granted, providing an
      audit trail for role changes without a separate history table.
    """

    __tablename__ = "assignment_approvers"
    __table_args__ = (
        # Prevent duplicate approver entries for the same assignment.
        UniqueConstraint("assignment_id", "approver_id", name="uq_assignment_approver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), nullable=False)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    assignment: Mapped["Assignment"] = relationship(back_populates="approvers")
    approver: Mapped["User"] = relationship(
        back_populates="assignment_approver_roles", foreign_keys=[approver_id]
    )


class Evidence(Base):
    """A piece of supporting evidence attached to an AccessRequest and/or Assignment.

    Evidence records store the location of a file (or external reference) together
    with structured metadata so that auditors can trace *what* was uploaded, *when*,
    and *by whom*, without parsing free-text fields.

    Normalization notes:
    - ``uploaded_by_email`` is denormalized (captured at upload time) for the same
      reason as ``AuditLog.actor_email``: the record must remain readable even if
      the uploader's account is later deleted or their email is changed.
    - ``uploaded_by_id`` is kept as a nullable FK so that live accounts can be
      navigated from the evidence record, while the denormalized email remains the
      reliable long-term audit field.
    - Both ``access_request_id`` and ``assignment_id`` are nullable at the database
      level to allow maximum flexibility, but the service layer enforces that at
      least one is provided when creating a new evidence record.
    - ``evidence_type`` is a constrained string rather than a FK to a lookup table;
      the enumeration is small and stable, so a separate table would add join cost
      with no normalisation benefit.
    - The parent relationships on ``AccessRequest`` and ``Assignment`` use
      ``cascade="save-update, merge"`` (no delete propagation) so that deleting
      one parent does not silently destroy Evidence that is still linked to the
      other parent.  Explicit orphan cleanup (both FKs NULL) must be handled at
      the service layer when evidence should be removed.

    Valid values for ``evidence_type``:
        document | screenshot | certificate | log | photo | other
    """

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Human-readable label for this piece of evidence.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Optional longer description or context note.
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Relative or absolute file-system path (or URL) to the stored evidence file.
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Constrained type string: document | screenshot | certificate | log | photo | other
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False, default="document")
    # Timestamp when the evidence was uploaded / recorded (UTC).
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # Nullable FK to the uploading user — nullable so system processes can create
    # evidence records without a user account.
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Denormalized email captured at upload time for long-term auditability.
    uploaded_by_email: Mapped[str] = mapped_column(String(255), nullable=False)

    # Optional FK to the AccessRequest this evidence supports.
    access_request_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("access_requests.id"), nullable=True
    )
    # Optional FK to the Assignment this evidence supports.
    assignment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assignments.id"), nullable=True
    )

    # Relationships
    uploaded_by: Mapped[Optional["User"]] = relationship(
        back_populates="uploaded_evidence", foreign_keys=[uploaded_by_id]
    )
    access_request_ref: Mapped[Optional["AccessRequest"]] = relationship(
        back_populates="evidence", foreign_keys=[access_request_id]
    )
    assignment_ref: Mapped[Optional["Assignment"]] = relationship(
        back_populates="evidence", foreign_keys=[assignment_id]
    )
