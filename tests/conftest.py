# -*- coding: utf-8 -*-
"""
Pytest configuration.

Integration tests use Flask's test client - no external browser drivers required.
"""

import pytest
import os
import tempfile
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from app.db import Base


@pytest.fixture()
def app():
    """Create and configure a new app instance for testing."""
    # Create a temporary database
    db_fd, db_path = tempfile.mkstemp()
    
    app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["WTF_CSRF_ENABLED"] = False  # Disable CSRF for testing
    
    # Import and initialize extensions
    from app import create_app as factory_create_app
    app = factory_create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["WTF_CSRF_ENABLED"] = False
    
    # Create tables
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    app.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    
    yield app
    
    # Cleanup
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture()
def client(app):
    """A test client for the app."""
    return app.test_client()


@pytest.fixture()
def runner(app):
    """A test CLI runner for the app."""
    return app.test_cli_runner()






