# -*- coding: utf-8 -*-
"""
Pytest configuration.

Integration tests use Flask's test client - no external browser drivers required.
"""

import pytest
import os

from app.db import Base


@pytest.fixture()
def app():
    """Create and configure a new app instance for testing."""
    # Set DATABASE_URL before create_app() so that the engine, session factory,
    # and Flask-Login's user_loader all share the same in-memory test database.
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    from app import create_app as factory_create_app
    app = factory_create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["WTF_CSRF_ENABLED"] = False

    # Ensure all tables exist on the app's configured engine.
    engine = getattr(app, "engine", None)
    if engine is not None:
        Base.metadata.create_all(bind=engine)

    yield app

    # Remove the override so it does not bleed into other test modules.
    os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def client(app):
    """A test client for the app."""
    return app.test_client()


@pytest.fixture()
def runner(app):
    """A test CLI runner for the app."""
    return app.test_cli_runner()






