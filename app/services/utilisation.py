# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:15:27 2026

@author: NBoyd1
"""

from datetime import datetime, timedelta
from sqlalchemy import select, func, Float, text
from sqlalchemy.orm import Session

from ..models import BookingRequest, BookingItem, Machine


def utilisation_last_days(db: Session, days: int = 30):
    since = datetime.utcnow() - timedelta(days=days)
    dialect_name = db.bind.dialect.name  # Detect the DB dialect

    # Pick the appropriate duration calculation (returns hours as a float).
    if dialect_name == "sqlite":
        # julianday returns fractional days; multiply by 24 to get hours.
        duration_expr = (
            func.julianday(BookingRequest.end_at)
            - func.julianday(BookingRequest.start_at)
        ) * 24.0
    elif dialect_name == "mssql":
        # DATEDIFF(minute, ...) returns an integer; divide by 60.0 for hours.
        duration_expr = (
            func.DATEDIFF(
                text("minute"),
                BookingRequest.start_at,
                BookingRequest.end_at,
            ) / 60.0
        ).cast(Float)
    else:
        raise NotImplementedError(
            f"utilisation_last_days() does not support the '{dialect_name}' "
            "dialect. Supported dialects: sqlite, mssql."
        )

    # By machine
    rows = db.execute(
        select(
            Machine.id,
            Machine.name,
            Machine.category,
            func.sum(duration_expr).label("hours")
        )
        .join(BookingItem, BookingItem.machine_id == Machine.id)
        .join(BookingRequest, BookingRequest.id == BookingItem.booking_id)
        .where(
            BookingRequest.status == "approved",
            BookingRequest.start_at >= since,
        )
        .group_by(Machine.id, Machine.name, Machine.category)
        .order_by(func.sum(duration_expr).desc())
    ).all()

    by_machine = [
        {
            "machine_id": r[0],
            "machine": r[1],
            "category": r[2],
            "hours": float(r[3] or 0),
        }
        for r in rows
    ][:15]

    # By category
    cat_rows = db.execute(
        select(
            Machine.category,
            func.sum(duration_expr).label("hours")
        )
        .join(BookingItem, BookingItem.machine_id == Machine.id)
        .join(BookingRequest, BookingRequest.id == BookingItem.booking_id)
        .where(
            BookingRequest.status == "approved",
            BookingRequest.start_at >= since,
        )
        .group_by(Machine.category)
        .order_by(func.sum(duration_expr).desc())
    ).all()

    by_category = [
        {"category": r[0], "hours": float(r[1] or 0)}
        for r in cat_rows
    ]

    return {
        "since": since,
        "by_machine": by_machine,
        "by_category": by_category,
    }