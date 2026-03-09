# -*- coding: utf-8 -*-
"""
Migration: add_evidence

Creates the ``evidence`` table to support advanced evidence storage,
linking evidence records to AccessRequests and/or Assignments with full
file-path, type, timestamp, and actor metadata.

Usage:
    python -m migrations.add_evidence [DATABASE_URL]

If DATABASE_URL is omitted the value of the DATABASE_URL environment variable
is used, falling back to ``sqlite:///app.db``.
"""

import os
import sys

from sqlalchemy import create_engine, inspect

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

    if "evidence" not in existing:
        Base.metadata.tables["evidence"].create(bind=engine)
        print("  created table: evidence")
    else:
        print("  table evidence already exists – skipping creation.")


if __name__ == "__main__":
    db_url_arg = sys.argv[1] if len(sys.argv) > 1 else None
    print("Running migration: add_evidence")
    run(db_url_arg)
    print("Done.")
