# -*- coding: utf-8 -*-
"""
Shared helpers for background job execution (Issue #26).

Concurrency strategy
--------------------
Each scheduled job MUST use exactly one database session per execution, opened
at the start and closed (committed or rolled-back) at the end.  This pattern
is enforced by the ``job_session`` context manager below.

Why this is safe under concurrency:

* ``max_instances=1`` in APScheduler prevents two instances of the same job
  from running simultaneously in the same process.  Even if the scheduler
  fires late, the job will not overlap with itself within a single process.
* ``coalesce=True`` collapses multiple missed firings into a single execution,
  so a backlog does not cause a burst of concurrent calls.
* ``misfire_grace_time=60`` ensures short-latency misfires are still executed
  rather than silently dropped.
* ``replace_existing=True`` makes job registration idempotent; re-calling
  ``create_app()`` will not register duplicate job definitions.
* ``job_session`` accepts an optional *job_name* parameter.  When provided,
  it acquires a process-level ``threading.Lock`` for that name before opening
  the session and releases it after closing.  This mirrors what APScheduler's
  ``max_instances=1`` already does and provides an explicit safety net for
  tests or edge cases where two threads call the same job function directly.
  Because the lock is per-job-name, distinct jobs never block each other.
* Idempotency guards in every job (AuditLog-based dedup, status-equality
  checks) ensure that if the same job *does* run twice—e.g. in tests, after a
  process restart, or after a missed-fire catch-up—it does not produce
  duplicate records.  The guard pattern is:

      existing = db.execute(select(AuditLog).where(...)).scalars().first()
      if existing:
          return  # already handled; nothing to do

* Transactions are kept short: the session is opened, work is done, and the
  session is committed (or rolled back) and closed before the function
  returns.  No long-running database row-locks are held between rows.
* A bounded time horizon is used when loading rows (e.g. ±24 h for booking
  monitoring) so the job never scans the entire table.

No Flask app context is required by any function in this module or in the
job functions that use it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Optional

# ---------------------------------------------------------------------------
# Per-job process-level locks
#
# In production, APScheduler's max_instances=1 guarantees only one instance
# of each job runs at a time.  These locks replicate that guarantee at the
# application level so that direct/test invocations are equally safe.
# ---------------------------------------------------------------------------
_job_locks: dict[str, threading.Lock] = {}
_job_locks_mutex = threading.Lock()


def _get_job_lock(job_name: str) -> threading.Lock:
    """Return (creating if necessary) the threading.Lock for *job_name*."""
    with _job_locks_mutex:
        if job_name not in _job_locks:
            _job_locks[job_name] = threading.Lock()
        return _job_locks[job_name]


@contextmanager
def job_session(SessionLocal, *, job_name: Optional[str] = None):
    """Context manager that owns a single database session for one job run.

    Opens a new session from *SessionLocal*, yields it to the caller, commits
    on a clean exit, rolls back on any exception (then re-raises), and always
    closes the session in the ``finally`` block.

    When *job_name* is provided a per-name ``threading.Lock`` is acquired
    before the session is opened and released after the session is closed.
    This serialises concurrent invocations of the same job within a single
    process, complementing APScheduler's ``max_instances=1`` setting.

    This is the **standard session lifecycle** that all background jobs MUST
    use so that:

    - every job run gets its own isolated session (no shared state between
      runs),
    - uncommitted changes are never left open on failure,
    - the connection is returned to the pool promptly after the job ends, and
    - concurrent direct invocations (e.g. in tests) are serialised safely.

    Usage::

        from app.automation.job_utils import job_session

        def run_my_job(SessionLocal, *, now=None):
            with job_session(SessionLocal, job_name="my_job") as db:
                # ... query and mutate via db ...
                # commit happens automatically on clean exit

    Parameters
    ----------
    SessionLocal:
        A SQLAlchemy ``sessionmaker`` (or ``scoped_session``) factory.
    job_name:
        Optional stable identifier for this job type.  When supplied,
        concurrent calls with the same *job_name* are serialised via a
        ``threading.Lock``.

    Yields
    ------
    sqlalchemy.orm.Session
        The open session.  Do **not** call ``commit()`` or ``close()``
        manually inside the ``with`` block; the context manager handles both.
    """
    lock = _get_job_lock(job_name) if job_name else None
    if lock is not None:
        lock.acquire()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        if lock is not None:
            lock.release()
