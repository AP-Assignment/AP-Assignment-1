# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:17:27 2026

@author: NBoyd1
"""

from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from ..forms import BookingForm
from ..models import Machine, BookingRequest, BookingItem, User, AuditLog
from ..services.booking_rules import validate_booking_window, machines_exist_and_available
from ..services.notifications import queue_notification

bp = Blueprint("bookings", __name__, url_prefix="/bookings")

@bp.get("/my")
@login_required
def my_bookings():
    with current_app.session_factory() as db:
        bookings = db.execute(
            select(BookingRequest)
            .options(
                selectinload(BookingRequest.items).selectinload(BookingItem.machine)
            )
            .where(BookingRequest.requester_id == current_user.id)
            .order_by(BookingRequest.start_at.desc())
        ).scalars().all()
    return render_template("my_bookings.html", bookings=bookings)

@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_booking():
    form = BookingForm()
    with current_app.session_factory() as db:
        machines = db.execute(select(Machine).where(Machine.status == "available").order_by(Machine.name)).scalars().all()
        form.machines.choices = [(m.id, f"{m.name} • {m.machine_type.upper()} • {m.site.city}") for m in machines]

        if form.validate_on_submit():
            ok, msg = validate_booking_window(form.start_at.data, form.end_at.data)
            if not ok:
                flash(msg, "warning")
                return render_template("new_booking.html", form=form)

            ids = list(dict.fromkeys(form.machines.data))
            ok2, msg2 = machines_exist_and_available(db, ids)
            if not ok2:
                flash(msg2, "warning")
                return render_template("new_booking.html", form=form)

            booking = BookingRequest(
                requester_id=current_user.id,
                start_at=form.start_at.data,
                end_at=form.end_at.data,
                purpose=form.purpose.data.strip(),
                status="pending",
            )
            db.add(booking)
            db.flush()

            for mid in ids:
                db.add(BookingItem(booking_id=booking.id, machine_id=mid))

            approvers = db.execute(select(User).where(User.role.in_(["approver", "admin"]), User.status == "active")).scalars().all()
            for a in approvers:
                queue_notification(db, a.id, f"New booking request #{booking.id} awaiting approval.")

            db.add(AuditLog(actor_email=current_user.email, action="booking_request", detail=f"Created booking request #{booking.id}"))
            db.commit()

            flash("Booking request submitted for approval.", "success")
            return redirect(url_for("bookings.my_bookings"))

    return render_template("new_booking.html", form=form)

@bp.post("/cancel/<int:booking_id>")
@login_required
def cancel_booking(booking_id: int):
    with current_app.session_factory() as db:
        b = db.get(BookingRequest, booking_id)
        if not b or b.requester_id != current_user.id:
            flash("Booking not found.", "danger")
            return redirect(url_for("bookings.my_bookings"))

        if b.status not in ["pending", "approved"]:
            flash("This booking cannot be cancelled.", "warning")
            return redirect(url_for("bookings.my_bookings"))

        b.status = "cancelled"
        b.cancelled_at = datetime.utcnow()
        db.add(AuditLog(actor_email=current_user.email, action="booking_cancel", detail=f"Cancelled booking #{b.id}"))
        db.commit()

    flash("Booking cancelled.", "info")
    return redirect(url_for("bookings.my_bookings"))

@bp.post("/checkin/<int:booking_id>")
@login_required
def check_in(booking_id: int):
    now = datetime.utcnow()
    with current_app.session_factory() as db:
        b = db.get(BookingRequest, booking_id)
        if not b or b.requester_id != current_user.id:
            flash("Booking not found.", "danger")
            return redirect(url_for("bookings.my_bookings"))

        if b.status != "approved":
            flash("Only approved bookings can be checked in.", "warning")
            return redirect(url_for("bookings.my_bookings"))

        if not (b.start_at <= now <= b.end_at):
            flash("You can only check in during the booking window.", "warning")
            return redirect(url_for("bookings.my_bookings"))

        b.checked_in = True
        db.add(AuditLog(actor_email=current_user.email, action="booking_checkin", detail=f"Checked in for booking #{b.id}"))
        db.commit()

    flash("Checked in successfully.", "success")
    return redirect(url_for("bookings.my_bookings"))
