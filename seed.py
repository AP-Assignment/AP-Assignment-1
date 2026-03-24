# -*- coding: utf-8 -*-
"""
Database initialization and seed-data script.

Design decisions
================
Idempotency
-----------
The function checks for existing ``Site`` rows before inserting any data.
If the table already contains rows the entire function returns early, so it is
safe to call at every application start-up (as ``run.py`` does) without risk
of duplicating records.

A single top-level idempotency guard (on ``sites``) is intentional: all
other entities (locations, machines, users, assignments, access requests and
their status-history rows) are inserted within the same database transaction
and depend on the IDs assigned to the earlier objects.  A fine-grained guard
per table would add complexity with no practical benefit – if the database is
partially seeded it is easier to drop and recreate it than to patch individual
tables.

Determinism vs randomness
--------------------------
Machine names, types, categories, and initial statuses are generated with
``random``.  A fixed seed (``random.seed(42)``) is applied at the start of
the function so that repeated runs against an *empty* database always produce
the same data – this keeps development environments consistent and makes
tests that inspect machine counts or names repeatable.

Timestamps
----------
All timestamps are stored as UTC (``datetime.utcnow()``).  The
``created_at``/``updated_at`` columns on AccessRequest and Assignment are
back-dated to simulate a realistic operational history (e.g. an approved
request was created two days ago and resolved yesterday).

Access-request status history
------------------------------
Every ``AccessRequest`` row is accompanied by one or more
``AccessRequestStatusHistory`` rows that mirror its full lifecycle.
- The *first* history row always has ``previous_status=None`` to represent
  the creation event (request submitted, no prior state).
- Subsequent rows carry ``previous_status`` copied from the preceding
  transition so that the chain can be reconstructed without joining back to
  the parent row (self-contained audit trail, as documented in the model).
- ``changed_by_id`` is ``None`` for system-generated transitions (e.g. the
  auto-expiry scenario) to match the nullable FK design on the model.

Realistic scenarios
-------------------
Five access-request scenarios are seeded to support manual testing and
automated-rule demonstrations:

  1. **Pending** – newly submitted, not yet actioned by an approver.
  2. **Approved** – approved by the approver user with a decision note.
  3. **Rejected** – rejected with a reason; the status history records the
     single pending→rejected transition.
  4. **Revoked** – previously approved, then revoked (pending→approved→revoked);
     the multi-step history demonstrates the full audit trail.
  5. **Expired** – system-expired after no approver action was taken;
     ``changed_by_id=None`` illustrates a system-generated transition.
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    AccessRequest,
    AccessRequestStatusHistory,
    Assignment,
    AssignmentApprover,
    Location,
    Machine,
    Site,
    User,
)
from app.security import hash_password

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MACHINE_CATEGORIES = [
    "Payments",
    "Devices",
    "Networking",
    "Core Platform",
    "Data Pipelines",
]
MACHINE_TYPES = ["lab", "virtual"]

# Probability that a machine is marked out_of_service (8 %).
OUT_OF_SERVICE_RATE = 0.08

# Random seed for deterministic machine generation across empty-DB runs.
RANDOM_SEED = 42

SITES_DATA = [
    (
        "Test Hub North", "MAN", "Manchester", "England",
        "1 Piccadilly Gardens, Manchester, M1 1RG", 53.4808, -2.2426,
    ),
    (
        "Test Hub South", "LON", "London", "England",
        "30 St Mary Axe, London, EC3A 8EP", 51.5072, -0.1276,
    ),
    (
        "Test Hub Central", "MKY", "Milton Keynes", "England",
        "600 Silbury Blvd, Milton Keynes, MK9 3AT", 52.0406, -0.7594,
    ),
    (
        "Test Hub West", "BRS", "Bristol", "England",
        "Temple Quay House, Bristol, BS1 6EG", 51.4545, -2.5879,
    ),
    (
        "Test Hub Scotland", "EDI", "Edinburgh", "Scotland",
        "1 Waverley Bridge, Edinburgh, EH1 1BQ", 55.9533, -3.1883,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_history(
    db: Session,
    access_request_id: int,
    previous_status: Optional[str],
    status: str,
    changed_by_id: Optional[int],
    note: Optional[str],
    changed_at: datetime,
) -> None:
    """Append a single AccessRequestStatusHistory row."""
    db.add(
        AccessRequestStatusHistory(
            access_request_id=access_request_id,
            previous_status=previous_status,
            status=status,
            changed_by_id=changed_by_id,
            note=note,
            changed_at=changed_at,
        )
    )


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------

def seed(db_url: str = "sqlite:///app.db") -> None:
    """Initialize the database schema and insert seed data.

    Parameters
    ----------
    db_url:
        SQLAlchemy connection URL.  Defaults to a local SQLite file so that
        the function can be called directly without any configuration.

    Notes
    -----
    The function is idempotent: it returns early (no-op) if ``sites`` data
    already exists in the database.  See module docstring for full design
    rationale.
    """
    engine = create_engine(
        db_url,
        future=True,
        connect_args=(
            {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        ),
    )
    # Use a local Random instance to avoid mutating the module-level RNG
    # state, which could affect other code that relies on random.
    rng = random.Random(RANDOM_SEED)

    try:
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, future=True)

        with SessionLocal() as db:
            # ------------------------------------------------------------------
            # Idempotency guard – return early if already seeded.
            # ------------------------------------------------------------------
            if db.execute(select(Site)).first():
                return

            # ------------------------------------------------------------------
            # Sites
            # ------------------------------------------------------------------
            sites: list[Site] = []
            for name, code, city, country, address, lat, lon in SITES_DATA:
                s = Site(
                    name=name,
                    code=code,
                    city=city,
                    country=country,
                    address=address,
                    lat=lat,
                    lon=lon,
                )
                db.add(s)
                sites.append(s)
            db.flush()

            # ------------------------------------------------------------------
            # Locations – two standard areas per site plus one sub-location
            #
            # Design: every site gets a "Lab" (physical benches) and a
            # "Virtual Lab" (hosted VMs).  A single Bay-A sub-location is added
            # beneath the lab to demonstrate the hierarchical parent/children
            # relationship.  The site_id is denormalized onto every Location row
            # (including sub-locations) so that site-scoped queries can filter
            # directly without a recursive join through the parent chain.
            # ------------------------------------------------------------------
            # Maps site.id → {"lab": location_id, "virtual": location_id}
            site_location_map: dict[int, dict[str, int]] = {}
            for site in sites:
                lab = Location(
                    name=f"{site.city} Lab",
                    code="LAB",
                    site_id=site.id,
                    floor="1",
                    description="Physical lab area with test machines.",
                )
                virtual = Location(
                    name=f"{site.city} Virtual Lab",
                    code="VLAB",
                    site_id=site.id,
                    description="Virtual machines hosted at this site.",
                )
                db.add(lab)
                db.add(virtual)
                db.flush()
                site_location_map[site.id] = {"lab": lab.id, "virtual": virtual.id}
                # Sub-location within the lab (demonstrates hierarchy)
                db.add(
                    Location(
                        name=f"{site.city} Lab \u2013 Bay A",
                        code="LAB-A",
                        site_id=site.id,
                        parent_id=lab.id,
                        floor="1",
                        description="Bay A \u2013 first row of test benches.",
                    )
                )

            # ------------------------------------------------------------------
            # Machines – 100 entries spread across sites and types
            # ------------------------------------------------------------------
            for i in range(1, 101):
                mtype = rng.choice(MACHINE_TYPES)
                site = rng.choice(sites)
                db.add(
                    Machine(
                        name=f"TM-{i:03d}",
                        machine_type=mtype,
                        category=rng.choice(MACHINE_CATEGORIES),
                        status=(
                            "out_of_service"
                            if rng.random() < OUT_OF_SERVICE_RATE
                            else "available"
                        ),
                        site_id=site.id,
                        location_id=site_location_map[site.id][mtype],
                    )
                )

            # ------------------------------------------------------------------
            # Users
            # ------------------------------------------------------------------
            admin = User(
                name="Admin User",
                email="admin@example.com",
                password_hash=hash_password("Admin123!"),
                team="Operations",
                role="admin",
                status="active",
                manager_email="director@example.com",
            )
            approver = User(
                name="Approver User",
                email="approver@example.com",
                password_hash=hash_password("Approver123!"),
                team="QA Governance",
                role="approver",
                status="active",
                manager_email="director@example.com",
            )
            regular_user = User(
                name="Standard User",
                email="user@example.com",
                password_hash=hash_password("User123!"),
                team="Engineering",
                role="user",
                status="active",
                manager_email="manager@example.com",
            )
            db.add_all([admin, approver, regular_user])
            db.flush()

            # ------------------------------------------------------------------
            # Assignments
            #
            # Design: three assignments are created to cover typical statuses in
            # the lifecycle (active, completed).  Each assignment is owned by the
            # standard user and has the approver user listed as an
            # AssignmentApprover.  The admin user is added as a second approver
            # on the first assignment to demonstrate the M:N approver relationship.
            # ------------------------------------------------------------------
            now = datetime.utcnow()

            assignment_payments = Assignment(
                title="Payments Regression Suite",
                description=(
                    "Full regression testing of the payments stack ahead of the "
                    "Q3 release.  Covers card-present, card-not-present, and "
                    "direct debit flows."
                ),
                owner_id=regular_user.id,
                status="active",
                created_at=now - timedelta(days=14),
            )
            assignment_networking = Assignment(
                title="Networking Load Tests",
                description=(
                    "End-to-end network performance benchmarks under simulated "
                    "peak load conditions for the new data-centre interconnect."
                ),
                owner_id=regular_user.id,
                status="active",
                created_at=now - timedelta(days=7),
            )
            assignment_platform = Assignment(
                title="Core Platform Audit",
                description=(
                    "Completed security and performance audit of the core "
                    "platform services.  All findings resolved."
                ),
                owner_id=admin.id,
                status="completed",
                created_at=now - timedelta(days=30),
                updated_at=now - timedelta(days=5),
            )
            db.add_all([assignment_payments, assignment_networking, assignment_platform])
            db.flush()

            # Approver roles – the approver user covers all three assignments;
            # the admin user is an additional approver on the first assignment.
            db.add_all(
                [
                    AssignmentApprover(
                        assignment_id=assignment_payments.id,
                        approver_id=approver.id,
                        assigned_at=now - timedelta(days=14),
                    ),
                    AssignmentApprover(
                        assignment_id=assignment_payments.id,
                        approver_id=admin.id,
                        assigned_at=now - timedelta(days=14),
                    ),
                    AssignmentApprover(
                        assignment_id=assignment_networking.id,
                        approver_id=approver.id,
                        assigned_at=now - timedelta(days=7),
                    ),
                    AssignmentApprover(
                        assignment_id=assignment_platform.id,
                        approver_id=approver.id,
                        assigned_at=now - timedelta(days=30),
                    ),
                ]
            )

            # ------------------------------------------------------------------
            # Access requests + status history
            #
            # Five scenarios are seeded (see module docstring for rationale).
            # Each request is linked to a site and an assignment where
            # appropriate.  Status-history rows are inserted immediately after
            # the flush that assigns the request's PK.
            # ------------------------------------------------------------------
            man_site, lon_site, mky_site = sites[0], sites[1], sites[2]

            # Scenario 1 – Pending (no approver action yet)
            req_pending = AccessRequest(
                requester_id=regular_user.id,
                site_id=man_site.id,
                assignment_id=assignment_payments.id,
                assignment="Payments Regression Suite",
                status="pending",
                created_at=now - timedelta(hours=3),
            )
            db.add(req_pending)
            db.flush()
            _add_history(
                db,
                access_request_id=req_pending.id,
                previous_status=None,
                status="pending",
                changed_by_id=regular_user.id,
                note="Access request submitted.",
                changed_at=now - timedelta(hours=3),
            )

            # Scenario 2 – Approved
            req_approved = AccessRequest(
                requester_id=regular_user.id,
                site_id=lon_site.id,
                assignment_id=assignment_networking.id,
                assignment="Networking Load Tests",
                status="approved",
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
                resolved_by_id=approver.id,
                resolved_at=now - timedelta(days=1),
                decision_note="Access granted for load-test window.",
            )
            db.add(req_approved)
            db.flush()
            _add_history(
                db,
                access_request_id=req_approved.id,
                previous_status=None,
                status="pending",
                changed_by_id=regular_user.id,
                note="Access request submitted.",
                changed_at=now - timedelta(days=2),
            )
            _add_history(
                db,
                access_request_id=req_approved.id,
                previous_status="pending",
                status="approved",
                changed_by_id=approver.id,
                note="Access granted for load-test window.",
                changed_at=now - timedelta(days=1),
            )

            # Scenario 3 – Rejected
            req_rejected = AccessRequest(
                requester_id=regular_user.id,
                site_id=mky_site.id,
                assignment_id=assignment_payments.id,
                assignment="Payments Regression Suite",
                status="rejected",
                created_at=now - timedelta(days=5),
                updated_at=now - timedelta(days=4),
                resolved_by_id=approver.id,
                resolved_at=now - timedelta(days=4),
                decision_note="Insufficient justification provided.",
            )
            db.add(req_rejected)
            db.flush()
            _add_history(
                db,
                access_request_id=req_rejected.id,
                previous_status=None,
                status="pending",
                changed_by_id=regular_user.id,
                note="Access request submitted.",
                changed_at=now - timedelta(days=5),
            )
            _add_history(
                db,
                access_request_id=req_rejected.id,
                previous_status="pending",
                status="rejected",
                changed_by_id=approver.id,
                note="Insufficient justification provided.",
                changed_at=now - timedelta(days=4),
            )

            # Scenario 4 – Revoked (approved then revoked; multi-step history)
            req_revoked = AccessRequest(
                requester_id=admin.id,
                site_id=man_site.id,
                assignment_id=assignment_platform.id,
                assignment="Core Platform Audit",
                status="revoked",
                created_at=now - timedelta(days=20),
                updated_at=now - timedelta(days=3),
                resolved_by_id=approver.id,
                resolved_at=now - timedelta(days=3),
                decision_note="Access revoked – audit cycle completed.",
            )
            db.add(req_revoked)
            db.flush()
            _add_history(
                db,
                access_request_id=req_revoked.id,
                previous_status=None,
                status="pending",
                changed_by_id=admin.id,
                note="Access request submitted.",
                changed_at=now - timedelta(days=20),
            )
            _add_history(
                db,
                access_request_id=req_revoked.id,
                previous_status="pending",
                status="approved",
                changed_by_id=approver.id,
                note="Access approved for audit period.",
                changed_at=now - timedelta(days=18),
            )
            _add_history(
                db,
                access_request_id=req_revoked.id,
                previous_status="approved",
                status="revoked",
                changed_by_id=approver.id,
                note="Access revoked – audit cycle completed.",
                changed_at=now - timedelta(days=3),
            )

            # Scenario 5 – Expired (system-generated, no human actor)
            req_expired = AccessRequest(
                requester_id=regular_user.id,
                site_id=lon_site.id,
                assignment="Ad-hoc site visit",
                status="expired",
                created_at=now - timedelta(days=10),
                updated_at=now - timedelta(days=2),
            )
            db.add(req_expired)
            db.flush()
            _add_history(
                db,
                access_request_id=req_expired.id,
                previous_status=None,
                status="pending",
                changed_by_id=regular_user.id,
                note="Access request submitted.",
                changed_at=now - timedelta(days=10),
            )
            _add_history(
                db,
                access_request_id=req_expired.id,
                previous_status="pending",
                status="expired",
                changed_by_id=None,  # system-generated transition
                note="Automatically expired after no approver action.",
                changed_at=now - timedelta(days=2),
            )

            db.commit()

    finally:
        engine.dispose()


if __name__ == "__main__":
    seed()
    print("Seed complete.")
