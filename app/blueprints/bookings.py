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
from ..models import Machine, BookingRequest, BookingItem, User, AuditLog, AccessRequest
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
        # Mapping exposed to the template for JS-driven checkbox visibility
        machine_type_map = {m.id: m.machine_type for m in machines}

        if form.validate_on_submit():
            ok, msg = validate_booking_window(form.start_at.data, form.end_at.data)
            if not ok:
                flash(msg, "warning")
                return render_template("new_booking.html", form=form, machine_type_map=machine_type_map)

            ids = list(dict.fromkeys(form.machines.data))
            ok2, msg2 = machines_exist_and_available(db, ids)
            if not ok2:
                flash(msg2, "warning")
                return render_template("new_booking.html", form=form, machine_type_map=machine_type_map)

            # Re-fetch selected machines to determine lab/site membership (anti-spoofing)
            selected_machines = db.execute(select(Machine).where(Machine.id.in_(ids))).scalars().all()
            contains_lab = any(m.machine_type == "lab" for m in selected_machines)

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

            # Create AccessRequest(s) only when selection contains lab machines
            # and the user explicitly requested site access (anti-spoofing enforced server-side)
            access_request_ids = []
            if form.request_access.data and contains_lab:
                lab_machines = [m for m in selected_machines if m.machine_type == "lab"]
                # Group lab machines by site; create one AccessRequest per site
                sites_seen: dict[int, str] = {}
                for m in lab_machines:
                    if m.site_id not in sites_seen:
                        sites_seen[m.site_id] = m.site.city
                for site_id, site_city in sites_seen.items():
                    ar = AccessRequest(
                        requester_id=current_user.id,
                        site_id=site_id,
                        assignment=f"Booking #{booking.id} – site access for {site_city}",
                        status="pending",
                    )
                    db.add(ar)
                    db.flush()
                    access_request_ids.append(ar.id)
                    db.add(AuditLog(
                        actor_email=current_user.email,
                        action="access_request_created_from_booking",
                        detail=(
                            f"booking_id={booking.id}, access_request_id={ar.id}, "
                            f"machine_ids={ids}, contains_lab={contains_lab}, site_id={site_id}"
                        ),
                    ))

            approvers = db.execute(select(User).where(User.role.in_(["approver", "admin"]), User.status == "active")).scalars().all()
            for a in approvers:
                queue_notification(db, a.id, f"New booking request #{booking.id} awaiting approval.")

            db.add(AuditLog(actor_email=current_user.email, action="booking_request", detail=f"Created booking request #{booking.id}"))
            db.commit()

            flash("Booking request submitted for approval.", "success")
            return redirect(url_for("bookings.my_bookings"))

    return render_template("new_booking.html", form=form, machine_type_map=machine_type_map)

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
