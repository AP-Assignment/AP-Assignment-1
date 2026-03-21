# -*- coding: utf-8 -*-
"""
Tests for the CSV export endpoints.
"""

import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Site, Machine, User, BookingRequest, BookingItem
from app.security import hash_password
from app.services.utilisation import utilisation_last_days


def _make_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session


def test_utilisation_export_data():
    """Ensure utilisation_last_days returns serialisable data suitable for CSV export."""
    Session = _make_db()

    with Session() as db:
        site = Site(name="Main", city="London", lat=51.5, lon=-0.1)
        db.add(site)
        db.flush()

        m1 = Machine(name="TM-001", machine_type="lab", category="Core", status="available", site_id=site.id)
        m2 = Machine(name="TM-002", machine_type="virtual", category="GPU", status="available", site_id=site.id)
        u = User(
            name="Alice",
            email="alice@example.com",
            password_hash=hash_password("Password123!"),
            team="ResearchTeam",
            role="user",
            status="active",
            manager_email="manager@example.com",
        )
        db.add_all([m1, m2, u])
        db.flush()

        start = datetime.utcnow() - timedelta(days=5)
        end = start + timedelta(hours=4)
        b = BookingRequest(requester_id=u.id, start_at=start, end_at=end, purpose="test", status="approved")
        db.add(b)
        db.flush()
        db.add_all([BookingItem(booking_id=b.id, machine_id=m1.id), BookingItem(booking_id=b.id, machine_id=m2.id)])
        db.commit()

        util = utilisation_last_days(db, days=30)

    # Verify the shape expected by the CSV export
    assert "by_machine" in util
    assert "by_category" in util
    assert "since" in util

    # Simulate CSV serialisation (mirrors export_utilisation route)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "name", "category", "hours"])
    for row in util["by_machine"]:
        writer.writerow(["by_machine", row["machine"], row["category"], row["hours"]])
    for row in util["by_category"]:
        writer.writerow(["by_category", row["category"], "", row["hours"]])

    output.seek(0)
    reader = csv.DictReader(output)
    rows = list(reader)

    machine_rows = [r for r in rows if r["section"] == "by_machine"]
    category_rows = [r for r in rows if r["section"] == "by_category"]

    assert len(machine_rows) == 2
    assert len(category_rows) == 2

    machine_names = {r["name"] for r in machine_rows}
    assert "TM-001" in machine_names
    assert "TM-002" in machine_names

    assert all(float(r["hours"]) > 0 for r in machine_rows)


def test_machines_export_data():
    """Ensure machine data can be serialised to CSV correctly."""
    Session = _make_db()

    with Session() as db:
        site = Site(name="HQ", city="Manchester", lat=53.4, lon=-2.2)
        db.add(site)
        db.flush()

        m = Machine(name="GPU-001", machine_type="lab", category="GPU", status="available", site_id=site.id)
        db.add(m)
        db.commit()

        from sqlalchemy import select
        from sqlalchemy.orm import joinedload
        machines = db.execute(
            select(Machine).options(joinedload(Machine.site)).order_by(Machine.name.asc())
        ).scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "name", "machine_type", "category", "site", "city", "status"])
        for mc in machines:
            writer.writerow([mc.id, mc.name, mc.machine_type, mc.category, mc.site.name, mc.site.city, mc.status])

    output.seek(0)
    reader = csv.DictReader(output)
    rows = list(reader)

    assert len(rows) == 1
    assert rows[0]["name"] == "GPU-001"
    assert rows[0]["city"] == "Manchester"
    assert rows[0]["status"] == "available"
