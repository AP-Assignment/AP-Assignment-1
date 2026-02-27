# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:31:25 2026

@author: NBoyd1
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from flask_login import UserMixin
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Site(Base):
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

    machines: Mapped[List["Machine"]] = relationship(back_populates="site")
    access_requests: Mapped[List["AccessRequest"]] = relationship(back_populates="site")
    locations: Mapped[List["Location"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Location(Base):
    """A named area within a Site (e.g. building, floor, room).

    Supports an arbitrary hierarchy through the self-referential ``parent_id``
    foreign key, allowing structures such as::

        Site → Building → Floor → Room

    The ``metadata_json`` column stores an optional JSON string for any
    additional key/value pairs needed by future extensions.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("site_id", "code", name="uq_location_site_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Short code unique within the site, e.g. "B1-F2-R03"
    code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    # Optional parent for hierarchical nesting (e.g. floor inside a building)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    floor: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Arbitrary JSON string for extensible metadata
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    site: Mapped["Site"] = relationship(back_populates="locations")
    parent: Mapped[Optional["Location"]] = relationship(
        back_populates="children", remote_side="Location.id"
    )
    children: Mapped[List["Location"]] = relationship(back_populates="parent")


class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    machine_type: Mapped[str] = mapped_column(String(20), nullable=False)  # lab | virtual
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="available")  # available | out_of_service
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)

    site: Mapped["Site"] = relationship(back_populates="machines")
    booking_items: Mapped[List["BookingItem"]] = relationship(back_populates="machine")


class User(Base, UserMixin):
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

    def is_active(self) -> bool:
        return self.status == "active"


class BookingRequest(Base):
    __tablename__ = "booking_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | approved | rejected | cancelled
    approver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    checked_in: Mapped[bool] = mapped_column(Boolean, default=False)
    no_show: Mapped[bool] = mapped_column(Boolean, default=False)

    requester: Mapped["User"] = relationship(back_populates="requests", foreign_keys=[requester_id])
    approver: Mapped[Optional["User"]] = relationship(back_populates="approvals", foreign_keys=[approver_id])
    items: Mapped[List["BookingItem"]] = relationship(back_populates="booking", cascade="all, delete-orphan")


class BookingItem(Base):
    __tablename__ = "booking_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking_requests.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)

    booking: Mapped["BookingRequest"] = relationship(back_populates="items")
    machine: Mapped["Machine"] = relationship(back_populates="booking_items")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="notifications")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(String(700), nullable=False)


class AccessRequest(Base):
    """A request by a user to be granted access to a site for a specific assignment."""

    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    site_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sites.id"), nullable=True)
    assignment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    # Brief description of the assignment or project this access is needed for
    assignment: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | approved | rejected | revoked
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)

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
    status_history: Mapped[List["AccessRequestStatusHistory"]] = relationship(
        back_populates="access_request", cascade="all, delete-orphan", order_by="AccessRequestStatusHistory.changed_at"
    )


class AccessRequestStatusHistory(Base):
    """Immutable audit trail of every status transition on an AccessRequest.

    Each row captures a single status change, recording both the previous and
    next status so that the full transition history can be reconstructed for
    compliance and audit purposes.
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
    """A formal assignment or project that users are working on, requiring site access."""

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

    owner: Mapped["User"] = relationship(back_populates="owned_assignments", foreign_keys=[owner_id])
    approvers: Mapped[List["AssignmentApprover"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )
    access_requests: Mapped[List["AccessRequest"]] = relationship(
        back_populates="assignment_ref", foreign_keys="AccessRequest.assignment_id"
    )


class AssignmentApprover(Base):
    """Records which users are designated approvers for a given assignment."""

    __tablename__ = "assignment_approvers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), nullable=False)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    assignment: Mapped["Assignment"] = relationship(back_populates="approvers")
    approver: Mapped["User"] = relationship(
        back_populates="assignment_approver_roles", foreign_keys=[approver_id]
    )
