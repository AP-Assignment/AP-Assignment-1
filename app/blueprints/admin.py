# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:18:54 2026

@author: NBoyd1
"""

import csv
import io
from sqlalchemy.orm import joinedload, selectinload
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, Response
from flask_login import login_required, current_user
from sqlalchemy import select, func
from ..models import User, BookingRequest, BookingItem, Machine, AuditLog
from ..services.booking_rules import has_conflicts_for_approved_bookings
from ..services.utilisation import utilisation_last_days
from ..services.notifications import queue_notification
from ..security import require_role


bp = Blueprint("admin", __name__, url_prefix="/admin")

def _require(allowed):
    if not require_role(current_user.role, allowed):
        flash("You do not have access to that page.", "danger")
        return False
    return True

@bp.get("/dashboard")
@login_required
def dashboard():
    if not _require({"approver", "admin"}):
        return redirect(url_for("bookings.my_bookings"))

    status = request.args.get("status", "pending")
    with current_app.session_factory() as db:
        util = utilisation_last_days(db, days=30)

        upcoming = db.execute(
            select(BookingRequest)
            .where(BookingRequest.start_at >= datetime.utcnow() - timedelta(days=1))
            .order_by(BookingRequest.start_at.asc())
            .limit(50)
        ).scalars().all()

        cancellations_30 = db.execute(
            select(func.count()).select_from(BookingRequest)
            .where(BookingRequest.status == "cancelled", BookingRequest.cancelled_at >= datetime.utcnow() - timedelta(days=30))
        ).scalar_one()

        no_shows_30 = db.execute(
            select(func.count()).select_from(BookingRequest)
            .where(BookingRequest.no_show.is_(True), BookingRequest.end_at >= datetime.utcnow() - timedelta(days=30))
        ).scalar_one()

        out_of_service = db.execute(
            select(func.count()).select_from(Machine).where(Machine.status == "out_of_service")
        ).scalar_one()

        pending_bookings = db.execute(
            select(BookingRequest)
            .options(
                selectinload(BookingRequest.requester),
                selectinload(BookingRequest.items).selectinload(BookingItem.machine),
            )
            .where(BookingRequest.status == status)
            .order_by(BookingRequest.start_at.asc())
            .limit(100)
        ).scalars().all()

    return render_template(
        "admin_dashboard.html",
        util=util,
        upcoming=upcoming,
        cancellations_30=cancellations_30,
        no_shows_30=no_shows_30,
        out_of_service=out_of_service,
        pending_bookings=pending_bookings,
        status=status,
    )

@bp.get("/users")
@login_required
def users():
    if not _require({"admin"}):
        return redirect(url_for("admin.dashboard"))
    with current_app.session_factory() as db:
        pending = db.execute(select(User).where(User.status == "pending").order_by(User.created_at.asc())).scalars().all()
        active = db.execute(select(User).where(User.status == "active").order_by(User.created_at.desc()).limit(50)).scalars().all()
    return render_template("admin_users.html", pending=pending, active=active)

@bp.post("/users/<int:user_id>/approve")
@login_required
def approve_user(user_id: int):
    if not _require({"admin"}):
        return redirect(url_for("admin.users"))
    with current_app.session_factory() as db:
        u = db.get(User, user_id)
        if not u:
            flash("User not found.", "danger")
            return redirect(url_for("admin.users"))
        u.status = "active"
        db.add(AuditLog(actor_email=current_user.email, action="user_approve", detail=f"Approved user {u.email}"))
        queue_notification(db, u.id, "Your account has been approved. You can now sign in.")
        db.commit()
    flash("User approved.", "success")
    return redirect(url_for("admin.users"))

@bp.post("/users/<int:user_id>/reject")
@login_required
def reject_user(user_id: int):
    if not _require({"admin"}):
        return redirect(url_for("admin.users"))
    with current_app.session_factory() as db:
        u = db.get(User, user_id)
        if not u:
            flash("User not found.", "danger")
            return redirect(url_for("admin.users"))
        u.status = "rejected"
        db.add(AuditLog(actor_email=current_user.email, action="user_reject", detail=f"Rejected user {u.email}"))
        queue_notification(db, u.id, "Your account request has been rejected. Contact an admin if you think this is an error.")
        db.commit()
    flash("User rejected.", "info")
    return redirect(url_for("admin.users"))

@bp.post("/booking/<int:booking_id>/approve")
@login_required
def approve_booking(booking_id: int):
    if not _require({"approver", "admin"}):
        return redirect(url_for("admin.dashboard"))

    with current_app.session_factory() as db:
        b = db.get(BookingRequest, booking_id)
        if not b or b.status != "pending":
            flash("Booking not found or not pending.", "warning")
            return redirect(url_for("admin.dashboard"))

        machine_ids = [it.machine_id for it in b.items]
        if has_conflicts_for_approved_bookings(db, machine_ids, b.start_at, b.end_at):
            b.status = "rejected"
            b.approver_id = current_user.id
            b.decision_note = "Rejected due to conflict with an existing approved booking."
            b.decided_at = datetime.utcnow()
            db.add(AuditLog(actor_email=current_user.email, action="booking_reject", detail=f"Rejected booking #{b.id} due to conflict"))
            queue_notification(db, b.requester_id, f"Booking #{b.id} rejected: conflict with an existing approved booking.")
            db.commit()
            flash("Cannot approve: conflict detected. The request has been rejected.", "danger")
            return redirect(url_for("admin.dashboard"))

        b.status = "approved"
        b.approver_id = current_user.id
        b.decision_note = "Approved"
        b.decided_at = datetime.utcnow()
        db.add(AuditLog(actor_email=current_user.email, action="booking_approve", detail=f"Approved booking #{b.id}"))
        queue_notification(db, b.requester_id, f"Booking #{b.id} approved.")
        db.commit()

    flash("Booking approved.", "success")
    return redirect(url_for("admin.dashboard"))

@bp.post("/booking/<int:booking_id>/reject")
@login_required
def reject_booking(booking_id: int):
    if not _require({"approver", "admin"}):
        return redirect(url_for("admin.dashboard"))
    note = (request.form.get("note") or "").strip()[:300]
    with current_app.session_factory() as db:
        b = db.get(BookingRequest, booking_id)
        if not b or b.status != "pending":
            flash("Booking not found or not pending.", "warning")
            return redirect(url_for("admin.dashboard"))
        b.status = "rejected"
        b.approver_id = current_user.id
        b.decision_note = note or "Rejected"
        b.decided_at = datetime.utcnow()
        db.add(AuditLog(actor_email=current_user.email, action="booking_reject", detail=f"Rejected booking #{b.id}"))
        queue_notification(db, b.requester_id, f"Booking #{b.id} rejected: {b.decision_note}")
        db.commit()
    flash("Booking rejected.", "info")
    return redirect(url_for("admin.dashboard"))

@bp.get("/export/bookings.csv")
@login_required
def export_bookings():
    if not _require({"admin"}):
        return redirect(url_for("admin.dashboard"))
    with current_app.session_factory() as db:
        rows = db.execute(select(BookingRequest).order_by(BookingRequest.start_at.desc()).limit(2000)).scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "requester_email", "start_at", "end_at", "status", "machines", "no_show", "cancelled_at", "decided_at", "decision_note"])

        for b in rows:
            requester = b.requester.email if b.requester else ""
            machines = "; ".join([it.machine.name for it in b.items])
            writer.writerow([b.id, requester, b.start_at.isoformat(), b.end_at.isoformat(), b.status, machines, b.no_show, b.cancelled_at, b.decided_at, b.decision_note])

    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=bookings_export.csv"})

@bp.post("/machines/<int:machine_id>/toggle_oos")
@login_required
def toggle_oos(machine_id: int):
    if not _require({"admin"}):
        return redirect(url_for("admin.dashboard"))
    with current_app.session_factory() as db:
        m = db.get(Machine, machine_id)
        if not m:
            flash("Machine not found.", "danger")
            return redirect(url_for("admin.inventory"))
        m.status = "available" if m.status == "out_of_service" else "out_of_service"
        db.add(AuditLog(actor_email=current_user.email, action="machine_toggle", detail=f"Toggled {m.name} to {m.status}"))
        db.commit()
    flash("Machine status updated.", "success")
    return redirect(url_for("admin.inventory"))

@bp.get("/inventory")
@login_required
def inventory():
    if not _require({"admin"}):
        return redirect(url_for("admin.dashboard"))
    q = (request.args.get("q") or "").strip()

    with current_app.session_factory() as db:
        stmt = (
            select(Machine)
            .options(joinedload(Machine.site))   # ✅ eager load relationship
            .order_by(Machine.name.asc())
        )

        if q:
            stmt = stmt.where(
                Machine.name.contains(q)
                | Machine.category.contains(q)
                | Machine.machine_type.contains(q)
            )

        machines = db.execute(stmt.limit(200)).scalars().all()

    return render_template("admin_inventory.html", machines=machines, q=q)
