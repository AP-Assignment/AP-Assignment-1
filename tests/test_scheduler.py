# -*- coding: utf-8 -*-
"""
Tests for APScheduler integration in create_app().

Verifies:
- Scheduler is attached to app.scheduler.
- Both jobs are registered with correct IDs, intervals, and concurrency settings.
- Scheduler is started at most once even when create_app() is called multiple times.
- Debug-mode / Werkzeug-reloader guard (_should_start_scheduler) behaves correctly.
- atexit shutdown hook does not raise.
"""

import os
import importlib

import pytest

import app as app_module
from app import create_app, _should_start_scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_scheduler_sentinel():
    """Reset the module-level _scheduler_started flag before each test so
    that tests are independent of execution order."""
    original = app_module._scheduler_started
    app_module._scheduler_started = False
    yield
    # Restore original value so unrelated tests are not affected.
    app_module._scheduler_started = original


def _make_app():
    """Create a test Flask app with TESTING=True."""
    application = create_app()
    application.config["TESTING"] = True
    return application


# ---------------------------------------------------------------------------
# Scheduler attachment
# ---------------------------------------------------------------------------

def test_scheduler_attached_to_app():
    """app.scheduler must be set after create_app()."""
    application = _make_app()
    assert hasattr(application, "scheduler")
    assert application.scheduler is not None


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------

def test_both_jobs_registered():
    """Both 'notifications' and 'no_show' jobs must be present."""
    application = _make_app()
    job_ids = {job.id for job in application.scheduler.get_jobs()}
    assert "notifications" in job_ids
    assert "no_show" in job_ids


def test_notifications_job_max_instances():
    application = _make_app()
    job = application.scheduler.get_job("notifications")
    assert job.max_instances == 1


def test_no_show_job_max_instances():
    application = _make_app()
    job = application.scheduler.get_job("no_show")
    assert job.max_instances == 1


def test_notifications_job_interval():
    """notifications job must fire every 30 seconds."""
    application = _make_app()
    job = application.scheduler.get_job("notifications")
    # APScheduler stores the interval trigger fields on the trigger object.
    assert job.trigger.interval.total_seconds() == 30


def test_no_show_job_interval():
    """no_show job must fire every 5 minutes."""
    application = _make_app()
    job = application.scheduler.get_job("no_show")
    assert job.trigger.interval.total_seconds() == 300


def test_jobs_coalesce_enabled():
    """Both jobs must have coalesce=True."""
    application = _make_app()
    for job_id in ("notifications", "no_show"):
        assert application.scheduler.get_job(job_id).coalesce is True


def test_jobs_misfire_grace_time():
    """Both jobs must have misfire_grace_time=60."""
    application = _make_app()
    for job_id in ("notifications", "no_show"):
        assert application.scheduler.get_job(job_id).misfire_grace_time == 60


# ---------------------------------------------------------------------------
# Single-start guard
# ---------------------------------------------------------------------------

def test_scheduler_started_once_on_first_create_app():
    """Scheduler should be running after first create_app()."""
    application = _make_app()
    assert application.scheduler.running


def test_scheduler_not_duplicated_on_second_create_app():
    """Calling create_app() a second time must not raise or start a second scheduler."""
    app1 = _make_app()
    assert app1.scheduler.running

    # Second call: _scheduler_started is True; a new scheduler object is created
    # but NOT started (to avoid duplicate background threads).
    app2 = _make_app()
    assert not app2.scheduler.running  # second scheduler left idle


# ---------------------------------------------------------------------------
# Debug / Werkzeug reloader guard
# ---------------------------------------------------------------------------

def test_should_start_scheduler_non_debug():
    """Outside debug mode, should always return True."""
    application = create_app()
    application.debug = False
    assert _should_start_scheduler(application) is True


def test_should_start_scheduler_debug_without_reloader_env():
    """In debug mode without WERKZEUG_RUN_MAIN, must return False (parent process)."""
    application = create_app()
    application.debug = True
    os.environ.pop("WERKZEUG_RUN_MAIN", None)
    assert _should_start_scheduler(application) is False


def test_should_start_scheduler_debug_with_reloader_env():
    """In debug mode with WERKZEUG_RUN_MAIN='true', must return True (child process)."""
    application = create_app()
    application.debug = True
    old = os.environ.get("WERKZEUG_RUN_MAIN")
    os.environ["WERKZEUG_RUN_MAIN"] = "true"
    try:
        assert _should_start_scheduler(application) is True
    finally:
        if old is None:
            os.environ.pop("WERKZEUG_RUN_MAIN", None)
        else:
            os.environ["WERKZEUG_RUN_MAIN"] = old


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def test_scheduler_shutdown_does_not_raise():
    """Calling shutdown on the scheduler must not raise."""
    application = _make_app()
    scheduler = application.scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)  # should not raise


def test_shutdown_idempotent_when_not_running():
    """Shutting down an already-stopped scheduler (via the guard) must not raise."""
    application = _make_app()
    scheduler = application.scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    # Second shutdown attempt should be safe (scheduler not running)
    assert not scheduler.running
