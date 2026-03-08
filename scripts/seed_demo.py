from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app import create_app
from app.models import BookingRequest, BookingItem, Machine, User


def _pick_requester(db) -> User:
    # Prefer the admin as a known user; otherwise just grab the first user.
    u = db.query(User).order_by(User.id.asc()).first()
    if not u:
        raise RuntimeError("No users found. Create at least one user account first.")
    return u


def _pick_machine(db) -> Machine:
    m = db.query(Machine).order_by(Machine.id.asc()).first()
    if not m:
        raise RuntimeError("No machines found. Create at least one machine first.")
    return m


def _make_booking(
    db,
    *,
    requester_id: int,
    machine_id: int,
    start_at: datetime,
    end_at: datetime,
    purpose: str,
    status: str,
    created_at: Optional[datetime] = None,
    checked_in: bool = False,
    no_show: bool = False,
) -> BookingRequest:
    b = BookingRequest(
        requester_id=requester_id,
        start_at=start_at,
        end_at=end_at,
        purpose=purpose,
        status=status,
        created_at=created_at or datetime.utcnow(),
        checked_in=checked_in,
        no_show=no_show,
    )
    db.add(b)
    db.flush()  # assigns b.id

    db.add(BookingItem(booking_id=b.id, machine_id=machine_id))
    return b


def seed():
    app = create_app()

    now = datetime.utcnow()

    with app.app_context():
        SessionLocal = app.session_factory

        with SessionLocal() as db:
            requester = _pick_requester(db)
            machine = _pick_machine(db)

            created = []

            # -------------------------
            # Scenario 1 (SLA buckets)
            # -------------------------
            # Put booking window in the future to avoid interfering with no-show logic.
            start_future = now + timedelta(days=3)
            end_future = start_future + timedelta(days=2)

            created.append(
                _make_booking(
                    db,
                    requester_id=requester.id,
                    machine_id=machine.id,
                    start_at=start_future,
                    end_at=end_future,
                    purpose="DEMO:SLA:pending_ok (created 1h ago)",
                    status="pending",
                    created_at=now - timedelta(hours=1),
                )
            )
            created.append(
                _make_booking(
                    db,
                    requester_id=requester.id,
                    machine_id=machine.id,
                    start_at=start_future,
                    end_at=end_future,
                    purpose="DEMO:SLA:warning (created 12h ago)",
                    status="pending",
                    created_at=now - timedelta(hours=12),
                )
            )
            created.append(
                _make_booking(
                    db,
                    requester_id=requester.id,
                    machine_id=machine.id,
                    start_at=start_future,
                    end_at=end_future,
                    purpose="DEMO:SLA:breach (created 72h ago)",
                    status="pending",
                    created_at=now - timedelta(hours=72),
                )
            )
            created.append(
                _make_booking(
                    db,
                    requester_id=requester.id,
                    machine_id=machine.id,
                    start_at=start_future,
                    end_at=end_future,
                    purpose="DEMO:SLA:expiry_candidate (created 8d ago)",
                    status="pending",
                    created_at=now - timedelta(days=8),
                )
            )

            # -------------------------
            # Scenario 3 (No-show)
            # -------------------------
            # Must be within the ±24h horizon and satisfy:
            # status=approved, checked_in=False, no_show=False, now > start_at + 5 minutes
            start_past = now - timedelta(minutes=10)
            end_past = now + timedelta(hours=1)

            created.append(
                _make_booking(
                    db,
                    requester_id=requester.id,
                    machine_id=machine.id,
                    start_at=start_past,
                    end_at=end_past,
                    purpose="DEMO:NO_SHOW:candidate (start 10m ago, not checked-in)",
                    status="approved",
                    created_at=now - timedelta(days=1),  # created_at doesn't matter for no-show rule
                    checked_in=False,
                    no_show=False,
                )
            )

            db.commit()

            print("\nSeed complete. Created BookingRequest IDs:")
            for b in created:
                print(f"- #{b.id}: {b.purpose} | status={b.status}")


if __name__ == "__main__":
    seed()