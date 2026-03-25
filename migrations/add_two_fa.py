# -*- coding: utf-8 -*-
"""Add 2FA (two-factor authentication) columns to users table."""

from sqlalchemy import text

def migrate(connection):
    """Add two_fa_secret and two_fa_enabled columns."""
    connection.execute(
        text("ALTER TABLE users ADD two_fa_secret VARCHAR(32) NULL")
    )
    connection.execute(
        text("ALTER TABLE users ADD two_fa_enabled BIT DEFAULT 0")
    )
    print("✓ Added 2FA columns to users table")

if __name__ == "__main__":
    from app.db import engine
    with engine.begin() as conn:
        migrate(conn)
