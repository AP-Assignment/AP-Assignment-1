# -*- coding: utf-8 -*-
"""
Migration: add_status_history_previous_status

Adds the ``previous_status`` column to the
``access_request_status_history`` table so that every audit-trail entry
records both the previous and next status of an access request.

Usage:
    python -m migrations.add_status_history_previous_status [DATABASE_URL]

If DATABASE_URL is omitted the value of the DATABASE_URL environment variable
is used, falling back to ``sqlite:///app.db``.
"""

import os
import sys

from sqlalchemy import create_engine, inspect, text

# Ensure the project root is on the path when running as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import Base  # noqa: E402 – import after path fix
import app.models  # noqa: F401 – registers all models with Base.metadata


def run(db_url: str | None = None) -> None:
    db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///app.db")
    engine = create_engine(
        db_url,
        future=True,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )

    inspector = inspect(engine)
    existing = inspector.get_table_names()

    if "access_request_status_history" not in existing:
        print("  table access_request_status_history does not exist – creating all tables.")
        Base.metadata.create_all(bind=engine)
        return

    existing_columns = {col["name"] for col in inspector.get_columns("access_request_status_history")}
    if "previous_status" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE access_request_status_history "
                    "ADD COLUMN previous_status VARCHAR(20)"
                )
            )
        print("  added column: access_request_status_history.previous_status")
    else:
        print("  column access_request_status_history.previous_status already exists – skipping.")


if __name__ == "__main__":
    db_url_arg = sys.argv[1] if len(sys.argv) > 1 else None
    print("Running migration: add_status_history_previous_status")
    run(db_url_arg)
    print("Done.")
