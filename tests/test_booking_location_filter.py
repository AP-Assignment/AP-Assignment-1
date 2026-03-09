# -*- coding: utf-8 -*-
"""
Tests for the 'Filter by location' list on the booking page.

Verifies that the locations dict used by the booking view is populated from
machine location assignments, so that the filter dropdown shows the correct
location options.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, joinedload

from app.db import Base
from app.models import Location, Machine, Site


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        yield session


@pytest.fixture()
def site(db):
    s = Site(name="Test Hub North", code="MAN", city="Manchester",
             country="England", address="1 Piccadilly Gardens, Manchester, M1 1RG",
             lat=53.4808, lon=-2.2426)
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def locations(db, site):
    """Create the two standard locations (lab and virtual lab) for the site."""
    lab = Location(name="Manchester Lab", code="LAB", site_id=site.id, floor="1",
                   description="Physical lab area.")
    vlab = Location(name="Manchester Virtual Lab", code="VLAB", site_id=site.id,
                    description="Virtual machines hosted at this site.")
    db.add(lab)
    db.add(vlab)
    db.flush()
    return {"lab": lab, "virtual": vlab}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _build_locations_dict(db):
    """Replicate the logic from bookings.new_booking() that builds the
    location filter options from the machines in the database."""
    machines = db.execute(
        select(Machine).options(joinedload(Machine.location))
    ).scalars().all()
    locations = {}
    for m in machines:
        if m.location_id and m.location:
            locations[m.location_id] = m.location.name
    return locations


def test_machines_without_location_produce_empty_filter(db, site):
    """Machines with no location_id should not contribute to the filter list."""
    db.add(Machine(name="TM-001", machine_type="lab", category="Payments",
                   status="available", site_id=site.id))
    db.add(Machine(name="TM-002", machine_type="virtual", category="Networking",
                   status="available", site_id=site.id))
    db.commit()

    result = _build_locations_dict(db)
    assert result == {}, "Expected empty locations dict when no machines have location_id"


def test_machines_with_locations_appear_in_filter(db, site, locations):
    """Machines assigned to locations should populate the filter dropdown."""
    lab_loc = locations["lab"]
    vlab_loc = locations["virtual"]

    db.add(Machine(name="TM-001", machine_type="lab", category="Payments",
                   status="available", site_id=site.id, location_id=lab_loc.id))
    db.add(Machine(name="TM-002", machine_type="virtual", category="Networking",
                   status="available", site_id=site.id, location_id=vlab_loc.id))
    db.commit()

    result = _build_locations_dict(db)

    assert lab_loc.id in result, "Lab location should appear in the filter list"
    assert vlab_loc.id in result, "Virtual lab location should appear in the filter list"
    assert result[lab_loc.id] == "Manchester Lab"
    assert result[vlab_loc.id] == "Manchester Virtual Lab"


def test_duplicate_machines_same_location_deduplicated(db, site, locations):
    """Multiple machines in the same location should produce only one filter entry."""
    lab_loc = locations["lab"]

    db.add(Machine(name="TM-001", machine_type="lab", category="Payments",
                   status="available", site_id=site.id, location_id=lab_loc.id))
    db.add(Machine(name="TM-002", machine_type="lab", category="Core Platform",
                   status="available", site_id=site.id, location_id=lab_loc.id))
    db.commit()

    result = _build_locations_dict(db)

    assert len(result) == 1, "Only one location entry expected for two machines sharing the same location"
    assert result[lab_loc.id] == "Manchester Lab"


def test_seed_assigns_location_to_all_machines(db, site, locations):
    """Simulates seed behaviour: every machine must have a location_id that
    matches its machine_type (lab → LAB location, virtual → VLAB location)."""
    lab_loc = locations["lab"]
    vlab_loc = locations["virtual"]
    type_to_location = {"lab": lab_loc.id, "virtual": vlab_loc.id}

    machines_data = [
        ("TM-001", "lab"),
        ("TM-002", "virtual"),
        ("TM-003", "lab"),
        ("TM-004", "virtual"),
    ]
    for name, mtype in machines_data:
        db.add(Machine(name=name, machine_type=mtype, category="Payments",
                       status="available", site_id=site.id,
                       location_id=type_to_location[mtype]))
    db.commit()

    # Every machine must have a location_id assigned
    all_machines = db.execute(select(Machine)).scalars().all()
    for m in all_machines:
        assert m.location_id is not None, f"Machine {m.name} has no location_id"
        assert m.location_id == type_to_location[m.machine_type], (
            f"Machine {m.name} (type={m.machine_type}) has wrong location_id"
        )

    # The filter dict should contain both locations
    result = _build_locations_dict(db)
    assert lab_loc.id in result
    assert vlab_loc.id in result
