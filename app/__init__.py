# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:09:11 2026

@author: NBoyd1
"""

from flask import Flask
from flask_login import LoginManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import atexit
import os

from .db import Base
from .models import User
from .services.notifications import process_notification_queue
from .services.no_show import mark_no_shows

login_manager = LoginManager()
login_manager.login_view = "auth.login"

# Module-level sentinel to prevent duplicate scheduler starts across multiple
# create_app() calls in the same process (e.g., during testing).
_scheduler_started = False


def _should_start_scheduler(app: Flask) -> bool:
    """Return True if the scheduler should be started in this process.

    In debug mode the Werkzeug reloader spawns a child process and sets
    WERKZEUG_RUN_MAIN="true".  We must only start the scheduler inside
    that child process so that the scheduler does not run twice.
    Outside debug mode the check is skipped and the scheduler always starts.
    """
    if app.debug:
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    return True


def create_app():
    global _scheduler_started

    load_dotenv()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    db_url = os.getenv("DATABASE_URL", "sqlite:///app.db")

    engine = create_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    SessionLocal = scoped_session(
        sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    )

    app.session_factory = SessionLocal
    app.engine = engine

    Base.metadata.create_all(bind=engine)

    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        with SessionLocal() as db:
            return db.get(User, int(user_id))

    from .blueprints.auth import bp as auth_bp
    from .blueprints.bookings import bp as bookings_bp
    from .blueprints.admin import bp as admin_bp
    from .blueprints.map import bp as map_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(map_bp)

    # ---------------------------------------------------------------------------
    # Background scheduler (advanced programming)
    #
    # Job configuration rationale:
    #   max_instances=1  – prevents overlapping executions if a job run takes
    #                       longer than the configured interval.
    #   coalesce=True    – if multiple misfires accumulate (e.g. during a
    #                       sleep/pause), execute the job only once on wake-up
    #                       rather than firing it for every missed run.
    #   misfire_grace_time=60 – tolerate up to 60 s of lateness before
    #                       treating a scheduled run as a misfire; reasonable
    #                       for 30 s / 5 min intervals.
    #   replace_existing=True – idempotent: re-calling create_app() will
    #                       replace the job definition instead of duplicating it.
    # ---------------------------------------------------------------------------
    scheduler = BackgroundScheduler(daemon=True)

    _job_defaults = dict(
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        replace_existing=True,
    )

    scheduler.add_job(
        lambda: process_notification_queue(SessionLocal),
        "interval",
        seconds=30,
        id="notifications",
        **_job_defaults,
    )
    scheduler.add_job(
        lambda: mark_no_shows(SessionLocal),
        "interval",
        minutes=5,
        id="no_show",
        **_job_defaults,
    )

    if not _scheduler_started and _should_start_scheduler(app):
        scheduler.start()
        _scheduler_started = True
        # The _scheduler_started sentinel ensures this block executes at most
        # once per process, so atexit.register is called exactly once.
        atexit.register(lambda: scheduler.shutdown(wait=False) if scheduler.running else None)

    app.scheduler = scheduler

    @app.teardown_appcontext
    def remove_session(_exc):
        SessionLocal.remove()

    return app
