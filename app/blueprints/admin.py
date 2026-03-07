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
from ..models import User, BookingRequest, BookingItem, Machine, AuditLog, AccessRequest
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

        # Automation monitoring: SLA counts for both BookingRequest and AccessRequest
        _now = datetime.utcnow()
        _warn_threshold   = _now - timedelta(hours=8)
        _breach_threshold = _now - timedelta(hours=48)

        br_pending = db.execute(
            select(func.count()).select_from(BookingRequest)
            .where(BookingRequest.status == "pending")
        ).scalar_one()

        br_sla_warning = db.execute(
            select(func.count()).select_from(BookingRequest)
            .where(
                BookingRequest.status == "pending",
                BookingRequest.created_at <= _warn_threshold,
                BookingRequest.created_at > _breach_threshold,
            )
        ).scalar_one()

        br_sla_breach = db.execute(
            select(func.count()).select_from(BookingRequest)
            .where(
                BookingRequest.status == "pending",
                BookingRequest.created_at <= _breach_threshold,
            )
        ).scalar_one()

        br_expired = db.execute(
            select(func.count()).select_from(BookingRequest)
            .where(BookingRequest.status == "expired")
        ).scalar_one()

        ar_pending = db.execute(
            select(func.count()).select_from(AccessRequest)
            .where(AccessRequest.status == "pending")
        ).scalar_one()

        ar_sla_warning = db.execute(
            select(func.count()).select_from(AccessRequest)
            .where(
                AccessRequest.status == "pending",
                AccessRequest.created_at <= _warn_threshold,
                AccessRequest.created_at > _breach_threshold,
            )
        ).scalar_one()

        ar_sla_breach = db.execute(
            select(func.count()).select_from(AccessRequest)
            .where(
                AccessRequest.status == "pending",
                AccessRequest.created_at <= _breach_threshold,
            )
        ).scalar_one()

        ar_expired = db.execute(
            select(func.count()).select_from(AccessRequest)
            .where(AccessRequest.status == "expired")
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

        # Access requests pending approval: only show those whose linked booking
        # is approved (approval ordering enforcement – issue #46).
        pending_access_requests = db.execute(
            select(AccessRequest)
            .join(BookingRequest, AccessRequest.booking_request_id == BookingRequest.id)
            .options(
                selectinload(AccessRequest.requester),
                selectinload(AccessRequest.booking_request),
            )
            .where(
                AccessRequest.status == "pending",
                BookingRequest.status == "approved",
            )
            .order_by(AccessRequest.created_at.asc())
            .limit(100)
        ).scalars().all()

    return render_template(
        "admin_dashboard.html",
        util=util,
        upcoming=upcoming,
        cancellations_30=cancellations_30,
        no_shows_30=no_shows_30,
        out_of_service=out_of_service,
        br_pending=br_pending,
        br_sla_warning=br_sla_warning,
        br_sla_breach=br_sla_breach,
        br_expired=br_expired,
        ar_pending=ar_pending,
        ar_sla_warning=ar_sla_warning,
        ar_sla_breach=ar_sla_breach,
        ar_expired=ar_expired,
        pending_bookings=pending_bookings,
        pending_access_requests=pending_access_requests,
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

        # Cascading rejection: auto-reject any linked pending AccessRequest.
        ar = db.execute(
            select(AccessRequest)
            .where(AccessRequest.booking_request_id == b.id, AccessRequest.status == "pending")
        ).scalar_one_or_none()
        if ar:
            ar.status = "rejected"
            ar.resolved_by_id = current_user.id
            ar.resolved_at = datetime.utcnow()
            ar.updated_at = datetime.utcnow()
            ar.decision_note = f"Auto-rejected: linked booking #{b.id} was rejected."
            db.add(AuditLog(
                actor_email=current_user.email,
                action="access_request_auto_rejected_due_to_booking_rejection",
                detail=f"access_request_id={ar.id}, booking_id={b.id}",
            ))
            queue_notification(
                db, ar.requester_id,
                f"Access request #{ar.id} auto-rejected because booking #{b.id} was rejected.",
            )

        db.commit()
    flash("Booking rejected.", "info")
    return redirect(url_for("admin.dashboard"))


@bp.post("/access-request/<int:ar_id>/approve")
@login_required
def approve_access_request(ar_id: int):
    if not _require({"approver", "admin"}):
        return redirect(url_for("admin.dashboard"))
    with current_app.session_factory() as db:
        ar = db.execute(
            select(AccessRequest).where(AccessRequest.id == ar_id)
        ).scalar_one_or_none()
        if not ar or ar.status != "pending":
            flash("Access request not found or not pending.", "warning")
            return redirect(url_for("admin.dashboard"))

        # Approval ordering: linked booking must be approved first.
        if ar.booking_request_id is not None:
            booking = db.get(BookingRequest, ar.booking_request_id)
            if not booking or booking.status != "approved":
                flash(
                    "Cannot approve access request: the linked booking has not been approved yet.",
                    "danger",
                )
                return redirect(url_for("admin.dashboard"))

        ar.status = "approved"
        ar.resolved_by_id = current_user.id
        ar.resolved_at = datetime.utcnow()
        ar.updated_at = datetime.utcnow()
        ar.decision_note = "Approved"
        db.add(AuditLog(
            actor_email=current_user.email,
            action="access_request_approved",
            detail=f"access_request_id={ar.id}, booking_id={ar.booking_request_id}",
        ))
        queue_notification(db, ar.requester_id, f"Access request #{ar.id} has been approved.")
        db.commit()

    flash("Access request approved.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.post("/access-request/<int:ar_id>/reject")
@login_required
def reject_access_request(ar_id: int):
    if not _require({"approver", "admin"}):
        return redirect(url_for("admin.dashboard"))
    note = (request.form.get("note") or "").strip()[:300]
    with current_app.session_factory() as db:
        ar = db.execute(
            select(AccessRequest).where(AccessRequest.id == ar_id)
        ).scalar_one_or_none()
        if not ar or ar.status != "pending":
            flash("Access request not found or not pending.", "warning")
            return redirect(url_for("admin.dashboard"))

        ar.status = "rejected"
        ar.resolved_by_id = current_user.id
        ar.resolved_at = datetime.utcnow()
        ar.updated_at = datetime.utcnow()
        ar.decision_note = note or "Rejected"
        db.add(AuditLog(
            actor_email=current_user.email,
            action="access_request_rejected",
            detail=f"access_request_id={ar.id}, booking_id={ar.booking_request_id}",
        ))
        queue_notification(
            db, ar.requester_id,
            f"Access request #{ar.id} rejected: {ar.decision_note}",
        )
        db.commit()

    flash("Access request rejected.", "info")
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

@bp.get("/export/utilisation.csv")
@login_required
def export_utilisation():
    if not _require({"admin"}):
        return redirect(url_for("admin.dashboard"))
    with current_app.session_factory() as db:
        util = utilisation_last_days(db, days=30)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["section", "name", "category", "hours"])
    for row in util["by_machine"]:
        writer.writerow(["by_machine", row["machine"], row["category"], row["hours"]])
    for row in util["by_category"]:
        writer.writerow(["by_category", row["category"], "", row["hours"]])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=utilisation_export.csv"},
    )

@bp.get("/export/machines.csv")
@login_required
def export_machines():
    if not _require({"admin"}):
        return redirect(url_for("admin.dashboard"))
    with current_app.session_factory() as db:
        machines = db.execute(
            select(Machine).options(joinedload(Machine.site)).order_by(Machine.name.asc())
        ).scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "name", "machine_type", "category", "site", "city", "status"])
        for m in machines:
            writer.writerow([m.id, m.name, m.machine_type, m.category, m.site.name, m.site.city, m.status])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=machines_export.csv"},
    )

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
