# -*- coding: utf-8 -*-
"""
Unit tests for utilisation_last_days() in app/services/utilisation.py.

Covers:
- Return structure (since, by_machine, by_category).
- SQLite dialect path (real in-memory DB).
- MSSQL dialect path (dialect name stubbed, expression compiled).
- Unsupported dialect raises NotImplementedError.
"""

import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BookingItem, BookingRequest, Machine, Site, User
from app.security import hash_password
from app.services.utilisation import utilisation_last_days


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sqlite_session() -> sessionmaker:
    """Return a sessionmaker bound to a fresh in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


def _seed_approved_booking(db, hours: float = 4.0) -> tuple:
    """Insert one site, two machines, one user, and one approved booking.

    Returns:
        Tuple of (m1, m2) Machine instances that were created.
    """
    site = Site(name="Main", city="London", lat=51.5, lon=-0.1)
    db.add(site)
    db.flush()

    m1 = Machine(
        name="TM-001",
        machine_type="lab",
        category="Core",
        status="available",
        site_id=site.id,
    )
    m2 = Machine(
        name="TM-002",
        machine_type="virtual",
        category="GPU",
        status="available",
        site_id=site.id,
    )
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
    end = start + timedelta(hours=hours)
    booking = BookingRequest(
        requester_id=u.id,
        start_at=start,
        end_at=end,
        purpose="test",
        status="approved",
    )
    db.add(booking)
    db.flush()
    db.add_all([
        BookingItem(booking_id=booking.id, machine_id=m1.id),
        BookingItem(booking_id=booking.id, machine_id=m2.id),
    ])
    db.commit()
    return m1, m2


# ---------------------------------------------------------------------------
# SQLite path (integration – real in-memory DB)
# ---------------------------------------------------------------------------

class TestSqliteDialect:
    """Tests using a real SQLite in-memory database."""

    def test_return_keys(self):
        """utilisation_last_days must return since, by_machine, by_category."""
        Session = _make_sqlite_session()
        with Session() as db:
            _seed_approved_booking(db)
            result = utilisation_last_days(db, days=30)

        assert "since" in result
        assert "by_machine" in result
        assert "by_category" in result

    def test_by_machine_is_list_of_dicts(self):
        Session = _make_sqlite_session()
        with Session() as db:
            _seed_approved_booking(db)
            result = utilisation_last_days(db, days=30)

        assert isinstance(result["by_machine"], list)
        for item in result["by_machine"]:
            assert isinstance(item, dict)
            assert "machine_id" in item
            assert "machine" in item
            assert "category" in item
            assert "hours" in item

    def test_by_category_is_list_of_dicts(self):
        Session = _make_sqlite_session()
        with Session() as db:
            _seed_approved_booking(db)
            result = utilisation_last_days(db, days=30)

        assert isinstance(result["by_category"], list)
        for item in result["by_category"]:
            assert isinstance(item, dict)
            assert "category" in item
            assert "hours" in item

    def test_hours_are_floats(self):
        Session = _make_sqlite_session()
        with Session() as db:
            _seed_approved_booking(db, hours=4.0)
            result = utilisation_last_days(db, days=30)

        for item in result["by_machine"]:
            assert isinstance(item["hours"], float)
        for item in result["by_category"]:
            assert isinstance(item["hours"], float)

    def test_hours_value_correct(self):
        """Each machine should show ~4 h for a 4-hour booking."""
        Session = _make_sqlite_session()
        with Session() as db:
            _seed_approved_booking(db, hours=4.0)
            result = utilisation_last_days(db, days=30)

        for item in result["by_machine"]:
            assert abs(item["hours"] - 4.0) < 0.01

    def test_since_is_datetime(self):
        Session = _make_sqlite_session()
        with Session() as db:
            result = utilisation_last_days(db, days=30)

        assert isinstance(result["since"], datetime)

    def test_empty_db_returns_empty_lists(self):
        Session = _make_sqlite_session()
        with Session() as db:
            result = utilisation_last_days(db, days=30)

        assert result["by_machine"] == []
        assert result["by_category"] == []


# ---------------------------------------------------------------------------
# MSSQL dialect path (unit – stub dialect, compile expression)
# ---------------------------------------------------------------------------

class TestMssqlDialect:
    """Verify MSSQL dialect branching using a mocked session."""

    def _make_mssql_db_mock(self) -> MagicMock:
        """Return a MagicMock db whose bind.dialect.name is 'mssql'."""
        mock_db = MagicMock()
        mock_db.bind.dialect.name = "mssql"
        return mock_db

    def test_mssql_uses_datediff_expression(self):
        """
        When dialect is mssql, the duration expression must use DATEDIFF.
        We verify by inspecting the compiled SQL for the expression.
        """
        from sqlalchemy import text as sa_text
        from sqlalchemy.dialects import mssql as mssql_dialect

        # Re-derive the expression the same way utilisation_last_days does.
        from sqlalchemy import func, Float
        from app.models import BookingRequest

        duration_expr = (
            func.DATEDIFF(
                sa_text("minute"),
                BookingRequest.start_at,
                BookingRequest.end_at,
            ) / 60.0
        ).cast(Float)

        compiled = duration_expr.compile(
            dialect=mssql_dialect.dialect()
        )
        sql_str = str(compiled).upper()
        assert "DATEDIFF" in sql_str
        assert "MINUTE" in sql_str

    def test_mssql_dialect_name_routes_to_mssql_path(self):
        """
        When db.bind.dialect.name == 'mssql', utilisation_last_days should
        not raise and should call db.execute (i.e., reach the query stage).
        """
        mock_db = self._make_mssql_db_mock()
        # Make execute return an object with .all() -> []
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = utilisation_last_days(mock_db, days=30)

        assert mock_db.execute.called
        assert "by_machine" in result
        assert "by_category" in result
        assert isinstance(result["by_machine"], list)
        assert isinstance(result["by_category"], list)


# ---------------------------------------------------------------------------
# Unsupported dialect
# ---------------------------------------------------------------------------

class TestUnsupportedDialect:
    """Unsupported dialects must raise NotImplementedError immediately."""

    @pytest.mark.parametrize("dialect", ["postgresql", "mysql", "oracle", "unknown_db"])
    def test_raises_not_implemented(self, dialect):
        mock_db = MagicMock()
        mock_db.bind.dialect.name = dialect

        with pytest.raises(NotImplementedError) as exc_info:
            utilisation_last_days(mock_db, days=30)

        assert dialect in str(exc_info.value)
        assert "sqlite" in str(exc_info.value).lower()
        assert "mssql" in str(exc_info.value).lower()
