# -*- coding: utf-8 -*-
"""
Pytest unit tests for app.automation.rules.evaluate_request.

These tests run without a Flask app context and without a database;
dummy request objects are plain dataclasses.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pytest

from app.automation.rules import (
    evaluate_request,
    NOTIFY,
    STATUS_CHANGE,
    SLA_WARNING_APPROVAL,
    SLA_BREACH_APPROVAL,
    AUTO_EXPIRE,
    ADMINS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class DummyRequest:
    """Minimal request-like object for unit testing."""
    status: str
    created_at: datetime
    id: Optional[int] = field(default=None)


_BASE = datetime(2024, 1, 1, 12, 0, 0)  # fixed reference point


def _req(status: str, age: timedelta, req_id: int = 1) -> DummyRequest:
    """Return a DummyRequest whose age relative to _BASE equals *age*."""
    return DummyRequest(status=status, created_at=_BASE - age, id=req_id)


def _now() -> datetime:
    return _BASE


# ---------------------------------------------------------------------------
# Non-pending status: no actions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["approved", "rejected", "expired", "cancelled"])
def test_non_pending_returns_no_actions(status):
    req = DummyRequest(status=status, created_at=_BASE - timedelta(days=10))
    result = evaluate_request(_now(), req)
    assert result["actions"] == []


# ---------------------------------------------------------------------------
# Pending under warning threshold: no actions
# ---------------------------------------------------------------------------

def test_pending_under_threshold_no_actions():
    req = _req("pending", timedelta(hours=7, minutes=59, seconds=59))
    result = evaluate_request(_now(), req)
    assert result["actions"] == []


# ---------------------------------------------------------------------------
# SLA warning
# ---------------------------------------------------------------------------

def test_pending_at_exactly_8h_warning():
    req = _req("pending", timedelta(hours=8))
    result = evaluate_request(_now(), req)
    actions = result["actions"]
    assert len(actions) == 1
    assert actions[0]["type"] == NOTIFY
    assert actions[0]["reason"] == SLA_WARNING_APPROVAL
    assert actions[0]["audience"] == ADMINS


def test_pending_between_8h_and_48h_warning():
    req = _req("pending", timedelta(hours=24))
    result = evaluate_request(_now(), req)
    actions = result["actions"]
    assert len(actions) == 1
    assert actions[0]["reason"] == SLA_WARNING_APPROVAL


def test_pending_just_under_48h_warning():
    req = _req("pending", timedelta(hours=47, minutes=59, seconds=59))
    result = evaluate_request(_now(), req)
    assert result["actions"][0]["reason"] == SLA_WARNING_APPROVAL


# ---------------------------------------------------------------------------
# SLA breach
# ---------------------------------------------------------------------------

def test_pending_at_exactly_48h_breach():
    req = _req("pending", timedelta(hours=48))
    result = evaluate_request(_now(), req)
    actions = result["actions"]
    assert len(actions) == 1
    assert actions[0]["type"] == NOTIFY
    assert actions[0]["reason"] == SLA_BREACH_APPROVAL
    assert actions[0]["audience"] == ADMINS


def test_pending_between_48h_and_7d_breach():
    req = _req("pending", timedelta(days=3))
    result = evaluate_request(_now(), req)
    assert result["actions"][0]["reason"] == SLA_BREACH_APPROVAL


def test_pending_just_under_7d_breach():
    req = _req("pending", timedelta(days=6, hours=23, minutes=59, seconds=59))
    result = evaluate_request(_now(), req)
    assert result["actions"][0]["reason"] == SLA_BREACH_APPROVAL


# ---------------------------------------------------------------------------
# Auto-expiry
# ---------------------------------------------------------------------------

def test_pending_at_exactly_7d_auto_expiry():
    req = _req("pending", timedelta(days=7))
    result = evaluate_request(_now(), req)
    actions = result["actions"]
    types = {a["type"] for a in actions}
    reasons = {a["reason"] for a in actions}
    assert STATUS_CHANGE in types
    assert NOTIFY in types
    assert AUTO_EXPIRE in reasons
    # Both a status change and a notification are returned
    assert len(actions) == 2


def test_pending_over_7d_auto_expiry():
    req = _req("pending", timedelta(days=30))
    result = evaluate_request(_now(), req)
    reasons = {a["reason"] for a in result["actions"]}
    assert AUTO_EXPIRE in reasons


def test_auto_expiry_status_change_has_new_status_expired():
    req = _req("pending", timedelta(days=7))
    result = evaluate_request(_now(), req)
    status_actions = [a for a in result["actions"] if a["type"] == STATUS_CHANGE]
    assert len(status_actions) == 1
    assert status_actions[0]["new_status"] == "expired"


# ---------------------------------------------------------------------------
# Precedence: most severe wins — no lower-severity actions alongside
# ---------------------------------------------------------------------------

def test_breach_does_not_also_warn():
    req = _req("pending", timedelta(hours=48))
    result = evaluate_request(_now(), req)
    reasons = [a["reason"] for a in result["actions"]]
    assert SLA_WARNING_APPROVAL not in reasons


def test_auto_expiry_does_not_also_breach_or_warn():
    req = _req("pending", timedelta(days=7))
    result = evaluate_request(_now(), req)
    reasons = [a["reason"] for a in result["actions"]]
    assert SLA_WARNING_APPROVAL not in reasons
    assert SLA_BREACH_APPROVAL not in reasons


# ---------------------------------------------------------------------------
# entity_type optional parameter (forward-compatibility)
# ---------------------------------------------------------------------------

def test_entity_type_parameter_accepted():
    req = _req("pending", timedelta(hours=8))
    result = evaluate_request(_now(), req, entity_type="AccessRequest")
    assert result["actions"][0]["reason"] == SLA_WARNING_APPROVAL


def test_entity_type_positional_accepted():
    req = _req("pending", timedelta(hours=48))
    result = evaluate_request(_now(), req, "BookingRequest")
    assert result["actions"][0]["reason"] == SLA_BREACH_APPROVAL


# ---------------------------------------------------------------------------
# Structured return shape
# ---------------------------------------------------------------------------

def test_result_has_actions_key():
    req = _req("pending", timedelta(hours=1))
    result = evaluate_request(_now(), req)
    assert "actions" in result
    assert isinstance(result["actions"], list)
