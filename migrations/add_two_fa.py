# -*- coding: utf-8 -*-
"""
Migration: add_two_fa

Adds two_fa_secret and two_fa_enabled columns to the users table.

Usage:
    python -m migrations.add_two_fa [DATABASE_URL]

If DATABASE_URL is omitted the value of the DATABASE_URL environment variable
is used, falling back to ``sqlite:///app.db``.
"""

import os
import sys

from sqlalchemy import create_engine, inspect, text

# Ensure the project root is on the path when running as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def migrate(connection):
    """Add two_fa_secret and two_fa_enabled columns if they do not already exist."""
    inspector = inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    added_any = False

    if "two_fa_secret" not in existing_columns:
        connection.execute(
            text("ALTER TABLE users ADD two_fa_secret VARCHAR(32) NULL")
        )
        print("✓ Added two_fa_secret column to users table")
        added_any = True

    if "two_fa_enabled" not in existing_columns:
        connection.execute(
            text("ALTER TABLE users ADD two_fa_enabled BIT DEFAULT 0")
        )
        print("✓ Added two_fa_enabled column to users table")
        added_any = True

    if not added_any:
        print("✓ 2FA columns already exist on users table; no changes made")


def run(db_url: str | None = None) -> None:
    """Entry point used by the migration runner; runs this migration idempotently."""
    db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///app.db")
    engine = create_engine(
        db_url,
        future=True,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    with engine.begin() as connection:
        migrate(connection)


if __name__ == "__main__":
    db_url_arg = sys.argv[1] if len(sys.argv) > 1 else None
    print("Running migration: add_two_fa")
    run(db_url_arg)
    print("Done.")
