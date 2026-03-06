# -*- coding: utf-8 -*-
"""
Tests for Issue #48: Show AccessRequest status on "My bookings".

Verifies that the "My bookings" page:
- Renders the new Access column header.
- Shows a blank Access cell for bookings with no linked AccessRequest.
- Shows the correct indicator and tooltip for each AccessRequest status
  (approved, rejected, pending, expired).
"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import create_app
from app.models import AccessRequest, BookingItem, BookingRequest, Machine, Site, User
from app.security import hash_password


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_app():
    """Create a Flask test app backed by an isolated per-test SQLite file."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    app = create_app()
    os.environ.pop("DATABASE_URL", None)

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    # Seed required data using the app's own session factory so that the
    # login_manager user_loader (which also uses that factory) can find users.
    with app.session_factory() as db:
        site = Site(name="Test Site", city="TestCity", lat=0.0, lon=0.0)
        db.add(site)
        db.flush()

        machine = Machine(
            name="LAB-T1",
            machine_type="lab",
            category="Core",
            status="available",
            site_id=site.id,
        )
        db.add(machine)
        db.flush()

        user = User(
            name="Test User",
            email="tester@example.com",
            password_hash=hash_password("Password1!"),
            team="QA",
            role="user",
            status="active",
            manager_email="mgr@example.com",
        )
        db.add(user)
        db.commit()

    yield app

    app.session_factory.remove()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client):
    """Log in as the seeded test user via the login form."""
    return client.post(
        "/login",
        data={"email": "tester@example.com", "password": "Password1!"},
        follow_redirects=True,
    )


def _get_user_and_machine(flask_app):
    with flask_app.session_factory() as db:
        user = db.execute(select(User).where(User.email == "tester@example.com")).scalar_one()
        machine = db.execute(select(Machine).where(Machine.name == "LAB-T1")).scalar_one()
        return user.id, machine.id


def _make_booking(flask_app, user_id, machine_id):
    start = datetime.utcnow() + timedelta(hours=1)
    end = start + timedelta(hours=2)
    with flask_app.session_factory() as db:
        b = BookingRequest(
            requester_id=user_id,
            start_at=start,
            end_at=end,
            purpose="Test booking",
            status="pending",
        )
        db.add(b)
        db.flush()
        db.add(BookingItem(booking_id=b.id, machine_id=machine_id))
        db.commit()
        return b.id


def _make_booking_with_access(flask_app, user_id, machine_id, access_status):
    booking_id = _make_booking(flask_app, user_id, machine_id)
    with flask_app.session_factory() as db:
        ar = AccessRequest(
            requester_id=user_id,
            booking_request_id=booking_id,
            assignment="Test access",
            status=access_status,
        )
        db.add(ar)
        db.commit()
    return booking_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_access_column_header_present(flask_app, client):
    """The 'My bookings' table must contain an 'Access' column header."""
    _login(client)

    resp = client.get("/bookings/my", follow_redirects=True)
    assert resp.status_code == 200
    assert b"<th>Access</th>" in resp.data


def test_no_access_request_shows_blank_cell(flask_app, client):
    """A booking with no linked AccessRequest must produce a blank Access cell."""
    user_id, machine_id = _get_user_and_machine(flask_app)
    _make_booking(flask_app, user_id, machine_id)

    _login(client)

    resp = client.get("/bookings/my", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode()
    # No tooltip text should appear because there's no access request.
    assert 'title="Access' not in html


def test_access_approved_shows_tick_and_tooltip(flask_app, client):
    """Approved AccessRequest must display ✓ with title='Access approved'."""
    user_id, machine_id = _get_user_and_machine(flask_app)
    _make_booking_with_access(flask_app, user_id, machine_id, "approved")

    _login(client)

    resp = client.get("/bookings/my", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'title="Access approved"' in html
    assert "✓" in html


def test_access_rejected_shows_cross_and_tooltip(flask_app, client):
    """Rejected AccessRequest must display ✗ with title='Access rejected'."""
    user_id, machine_id = _get_user_and_machine(flask_app)
    _make_booking_with_access(flask_app, user_id, machine_id, "rejected")

    _login(client)

    resp = client.get("/bookings/my", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'title="Access rejected"' in html
    assert "✗" in html


def test_access_pending_shows_dots_and_tooltip(flask_app, client):
    """Pending AccessRequest must display '...' with title='Access pending'."""
    user_id, machine_id = _get_user_and_machine(flask_app)
    _make_booking_with_access(flask_app, user_id, machine_id, "pending")

    _login(client)

    resp = client.get("/bookings/my", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'title="Access pending"' in html
    assert 'title="Access pending"' in html and ">...</span>" in html


def test_access_expired_shows_clock_and_tooltip(flask_app, client):
    """Expired AccessRequest must display 🕒 with title='Access expired'."""
    user_id, machine_id = _get_user_and_machine(flask_app)
    _make_booking_with_access(flask_app, user_id, machine_id, "expired")

    _login(client)

    resp = client.get("/bookings/my", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'title="Access expired"' in html
    assert "🕒" in html


