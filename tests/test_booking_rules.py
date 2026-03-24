# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:24:44 2026

@author: NBoyd1
"""

from datetime import datetime, timedelta
from app.services.booking_rules import validate_booking_window

def test_validate_booking_window_rejects_past():
    start = datetime.utcnow() - timedelta(hours=1)
    end = datetime.utcnow() + timedelta(hours=1)
    ok, msg = validate_booking_window(start, end)
    assert not ok
    assert "future" in msg.lower()

def test_validate_booking_window_rejects_too_far():
    start = datetime.utcnow() + timedelta(days=120)
    end = start + timedelta(hours=1)
    ok, msg = validate_booking_window(start, end)
    assert not ok
    assert "90" in msg

def test_validate_booking_window_accepts_valid():
    start = datetime.utcnow() + timedelta(hours=2)
    end = start + timedelta(hours=2)
    ok, msg = validate_booking_window(start, end)
    assert ok
    assert msg is None

def test_validate_booking_window_rejects_over_28_days():
    start = datetime.utcnow() + timedelta(hours=1)
    end = start + timedelta(days=29)
    ok, msg = validate_booking_window(start, end)
    assert not ok
    assert "28" in msg

def test_validate_booking_window_accepts_exactly_28_days():
    start = datetime.utcnow() + timedelta(hours=1)
    end = start + timedelta(days=28)
    ok, msg = validate_booking_window(start, end)
    assert ok
    assert msg is None

def test_validate_booking_window_accepts_under_28_days():
    start = datetime.utcnow() + timedelta(hours=1)
    end = start + timedelta(days=27, hours=23)
    ok, msg = validate_booking_window(start, end)
    assert ok
    assert msg is None
