# -*- coding: utf-8 -*-
"""
Deterministic, testable SLA rule-evaluation module.

Evaluates SLA actions for any request-like object that exposes:
  - status     (str)
  - created_at (datetime)
  - id         (any, optional — for traceability)

No database reads or writes; no Flask app context required.

Thresholds (docs/automation_rules.md v1.1):
  Warning    : created_at + 8 hours
  Breach     : created_at + 48 hours
  Auto-expiry: created_at + 7 days

Priority (most severe wins):
  AUTO_EXPIRE > SLA_BREACH_APPROVAL > SLA_WARNING_APPROVAL > OK
"""

from datetime import datetime, timedelta
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_WARN_THRESHOLD   = timedelta(hours=8)
_BREACH_THRESHOLD = timedelta(hours=48)
_EXPIRY_THRESHOLD = timedelta(days=7)

# ---------------------------------------------------------------------------
# Action / reason constants
# ---------------------------------------------------------------------------
NOTIFY         = "NOTIFY"
STATUS_CHANGE  = "STATUS_CHANGE"

SLA_WARNING_APPROVAL = "SLA_WARNING_APPROVAL"
SLA_BREACH_APPROVAL  = "SLA_BREACH_APPROVAL"
AUTO_EXPIRE          = "AUTO_EXPIRE"

ADMINS = "ADMINS"


def _make_notify(reason: str) -> dict:
    return {"type": NOTIFY, "reason": reason, "audience": ADMINS}


def _make_status_change(reason: str, new_status: str) -> dict:
    return {"type": STATUS_CHANGE, "reason": reason, "new_status": new_status}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_request(now: datetime, request: Any, entity_type: Optional[str] = None) -> dict:
    """Evaluate SLA rules for *request* at the given *now* timestamp.

    Parameters
    ----------
    now : datetime
        The current UTC time used for age calculation (injected for testability).
    request : any
        A request-like object with at minimum ``status`` (str) and
        ``created_at`` (datetime) attributes.
    entity_type : str, optional
        Ignored by the current rule set but accepted for forward-compatibility.

    Returns
    -------
    dict
        ``{"actions": [...]}`` where each action is a dict with at minimum
        ``type`` and ``reason`` keys.  Returns an empty actions list when the
        request is not ``pending`` or no threshold has been crossed.
    """
    if request.status != "pending":
        return {"actions": []}

    age = now - request.created_at

    if age >= _EXPIRY_THRESHOLD:
        return {
            "actions": [
                _make_status_change(AUTO_EXPIRE, "expired"),
                _make_notify(AUTO_EXPIRE),
            ]
        }

    if age >= _BREACH_THRESHOLD:
        return {
            "actions": [
                _make_notify(SLA_BREACH_APPROVAL),
            ]
        }

    if age >= _WARN_THRESHOLD:
        return {
            "actions": [
                _make_notify(SLA_WARNING_APPROVAL),
            ]
        }

    return {"actions": []}
