# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:14:21 2026

@author: NBoyd1
"""

from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import BookingRequest, BookingItem, Machine

MAX_DAYS_AHEAD = 90
MAX_BOOKING_DAYS = 28

def validate_booking_window(start_at: datetime, end_at: datetime) -> tuple[bool, str | None]:
    now = datetime.utcnow()
    if start_at < now - timedelta(minutes=1):
        return False, "Start time must be in the future."
    if start_at > now + timedelta(days=MAX_DAYS_AHEAD):
        return False, f"Bookings can only be made up to {MAX_DAYS_AHEAD} days ahead."
    if end_at <= start_at:
        return False, "End time must be after start time."
    if end_at - start_at > timedelta(days=MAX_BOOKING_DAYS):
        return False, "Maximum booking time is 28 days."
    return True, None

def machines_exist_and_available(db: Session, machine_ids: list[int]) -> tuple[bool, str | None]:
    if not machine_ids:
        return False, "Select at least one machine."
    machines = db.execute(select(Machine).where(Machine.id.in_(machine_ids))).scalars().all()
    if len(machines) != len(set(machine_ids)):
        return False, "One or more machines do not exist."
    if any(m.status != "available" for m in machines):
        return False, "One or more selected machines are out of service."
    return True, None

def has_conflicts_for_approved_bookings(db: Session, machine_ids: list[int], start_at: datetime, end_at: datetime) -> bool:
    # overlap rule: existing.start < new.end AND existing.end > new.start
    q = (
        select(BookingRequest.id)
        .join(BookingItem, BookingItem.booking_id == BookingRequest.id)
        .where(
            BookingRequest.status == "approved",
            BookingItem.machine_id.in_(machine_ids),
            BookingRequest.start_at < end_at,
            BookingRequest.end_at > start_at,
        )
        .limit(1)
    )
    return db.execute(q).first() is not None
