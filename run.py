# -*- coding: utf-8 -*-
"""
Created on Thu Jan  8 09:56:32 2026

@author: NBoyd1
"""

import os

from app import create_app
from seed import seed


def _bootstrap_local_db() -> None:
    """Seed a local SQLite database on first run.

    Only runs when DATABASE_URL points at a SQLite file that does not yet
    exist. Non-SQLite URLs (cloud/Gunicorn deployments) are left untouched.
    The Werkzeug reloader spawns a child process with WERKZEUG_RUN_MAIN=true;
    seeding only in the outer process avoids a double-seed race condition
    (the child finds the file already present and skips naturally).
    """
    db_url = os.getenv("DATABASE_URL", "sqlite:///app.db")
    if not db_url.startswith("sqlite:///"):
        return
    # Skip in the Werkzeug reloader child process - the outer process already
    # created/seeded the file before the child starts.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        return
    db_path = db_url[len("sqlite:///"):]
    if not os.path.exists(db_path):
        seed(db_url)


_bootstrap_local_db()
app = create_app()

if __name__ == "__main__":
    app.run(debug=True)