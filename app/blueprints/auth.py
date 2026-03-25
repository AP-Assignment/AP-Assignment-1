# -*- coding: utf-8 -*-
"""Authentication and registration routes."""

from flask import Blueprint, render_template, redirect, url_for, flash, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import select
from ..forms import RegisterForm, LoginForm, TOTPVerificationForm
from ..models import User, AuditLog
from ..security import hash_password, verify_password
import pyotp
import time
import qrcode
from io import BytesIO
import base64

bp = Blueprint("auth", __name__)

@bp.get("/")
def home():
    return render_template("home.html")

@bp.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        with current_app.session_factory() as db:
            exists = db.execute(select(User).where(User.email == form.email.data.lower())).scalar_one_or_none()
            if exists:
                flash("An account with that email already exists.", "warning")
                return render_template("register.html", form=form)

            user = User(
                name=form.name.data.strip(),
                email=form.email.data.lower(),
                password_hash=hash_password(form.password.data),
                team=form.team.data.strip(),
                manager_email=form.manager_email.data.lower(),
                role="user",
                status="pending",
            )
            db.add(user)
            db.add(AuditLog(actor_email=user.email, action="register", detail="User registered; awaiting manager approval"))
            db.commit()

        flash("Account created. Your manager must approve your access before you can sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html", form=form)

@bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        with current_app.session_factory() as db:
            user = db.execute(select(User).where(User.email == form.email.data.lower())).scalar_one_or_none()
            if not user or not verify_password(user.password_hash, form.password.data):
                flash("Invalid email or password.", "danger")
                return render_template("login.html", form=form)

            if user.status != "active":
                flash("Your account is not active yet. Please wait for manager approval.", "warning")
                return render_template("login.html", form=form)

            # If user has 2FA enabled, redirect to verification
            if user.two_fa_enabled:
                session["pending_user_id"] = user.id
                session["pending_user_email"] = user.email
                return redirect(url_for("auth.verify_totp"))

            login_user(user)
            db.add(AuditLog(actor_email=user.email, action="login", detail="User signed in"))
            db.commit()

        return redirect(url_for("bookings.my_bookings"))

    return render_template("login.html", form=form)

@bp.get("/logout")
@login_required
def logout():
    email = current_user.email
    logout_user()
    flash("Signed out.", "info")
    with current_app.session_factory() as db:
        db.add(AuditLog(actor_email=email, action="logout", detail="User signed out"))
        db.commit()
    return redirect(url_for("auth.home"))


@bp.route("/verify-totp", methods=["GET", "POST"])
def verify_totp():
    """Verify TOTP token during login with brute-force protection."""
    if "pending_user_id" not in session:
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("auth.login"))

    # Brute-force protection: track failed attempts
    MAX_ATTEMPTS = 5
    failed_attempts = session.get("totp_failed_attempts", 0)
    
    if failed_attempts >= MAX_ATTEMPTS:
        # Clear the pending session and force re-login
        session.pop("pending_user_id", None)
        session.pop("pending_user_email", None)
        session.pop("totp_failed_attempts", None)
        flash("Too many failed verification attempts. Please log in again.", "danger")
        return redirect(url_for("auth.login"))

    form = TOTPVerificationForm()
    if form.validate_on_submit():
        # Strip whitespace from token
        token = form.token.data.strip()
        
        with current_app.session_factory() as db:
            user = db.execute(select(User).where(User.id == session["pending_user_id"])).scalar_one_or_none()
            if not user or not user.verify_totp(token):
                # Increment failed attempts and add delay
                failed_attempts += 1
                session["totp_failed_attempts"] = failed_attempts
                
                # Small delay after failure (0.5 seconds) to slow brute-force
                time.sleep(0.5)
                
                flash("Invalid authentication code.", "danger")
                
                # Check if we've hit max attempts after this failure
                if failed_attempts >= MAX_ATTEMPTS:
                    # Clear the pending session and force re-login
                    session.pop("pending_user_id", None)
                    session.pop("pending_user_email", None)
                    session.pop("totp_failed_attempts", None)
                    flash("Too many failed verification attempts. Please log in again.", "danger")
                    return redirect(url_for("auth.login"))
                
                return render_template("verify_totp.html", form=form)

            # Success: reset counter and log in
            login_user(user)
            db.add(AuditLog(actor_email=user.email, action="login", detail="User signed in with 2FA"))
            db.commit()

        session.pop("pending_user_id", None)
        session.pop("pending_user_email", None)
        session.pop("totp_failed_attempts", None)  # Clear failed attempts on success
        return redirect(url_for("bookings.my_bookings"))

    return render_template("verify_totp.html", form=form)


@bp.route("/setup-2fa", methods=["GET", "POST"])
@login_required
def setup_2fa():
    """Setup 2FA for the current user."""
    if current_user.two_fa_enabled:
        flash("2FA is already enabled on your account.", "info")
        return redirect(url_for("bookings.my_bookings"))

    form = TOTPVerificationForm()
    
    # Generate new secret if not already in session
    if "temp_2fa_secret" not in session:
        # Generate secret outside ORM to avoid accidental persistence
        session["temp_2fa_secret"] = pyotp.random_base32()
    
    # Create a temporary TOTP object for QR generation (not stored on ORM)
    temp_secret = session["temp_2fa_secret"]
    temp_totp = pyotp.TOTP(temp_secret)
    provisioning_uri = temp_totp.provisioning_uri(name=current_user.email, issuer_name="AP Assignment System")
    
    # Generate QR code
    qr = qrcode.QRCode()
    qr.add_data(provisioning_uri)
    qr.make()
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64 for embedding in HTML
    img_io = BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)
    qr_code_b64 = base64.b64encode(img_io.getvalue()).decode()

    return render_template("setup_2fa.html", secret=temp_secret, qr_code=qr_code_b64, form=form)


@bp.route("/confirm-2fa", methods=["POST"])
@login_required
def confirm_2fa():
    """Confirm 2FA setup with a valid token."""
    form = TOTPVerificationForm()
    
    if not form.validate_on_submit():
        flash("Invalid authentication code.", "danger")
        return redirect(url_for("auth.setup_2fa"))

    if "temp_2fa_secret" not in session:
        flash("2FA setup session expired.", "warning")
        return redirect(url_for("auth.setup_2fa"))

    # Verify the token - strip whitespace before verification
    token = form.token.data.strip()
    totp = pyotp.TOTP(session["temp_2fa_secret"])
    if not totp.verify(token):
        flash("Invalid authentication code. Please try again.", "danger")
        return redirect(url_for("auth.setup_2fa"))

    # Enable 2FA
    with current_app.session_factory() as db:
        user = db.execute(select(User).where(User.id == current_user.id)).scalar_one_or_none()
        user.two_fa_secret = session["temp_2fa_secret"]
        user.two_fa_enabled = True
        db.add(AuditLog(actor_email=user.email, action="enable_2fa", detail="2FA enabled on account"))
        db.commit()

    session.pop("temp_2fa_secret", None)
    flash("2FA has been successfully enabled on your account.", "success")
    return redirect(url_for("bookings.my_bookings"))


@bp.route("/disable-2fa", methods=["POST"])
@login_required
def disable_2fa():
    """Disable 2FA for the current user."""
    with current_app.session_factory() as db:
        user = db.execute(select(User).where(User.id == current_user.id)).scalar_one_or_none()
        user.two_fa_enabled = False
        user.two_fa_secret = None
        db.add(AuditLog(actor_email=user.email, action="disable_2fa", detail="2FA disabled on account"))
        db.commit()

    flash("2FA has been disabled on your account.", "info")
    return redirect(url_for("bookings.my_bookings"))

