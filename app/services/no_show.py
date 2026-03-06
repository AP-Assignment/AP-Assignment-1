# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:16:22 2026

@author: NBoyd1
"""

def mark_no_shows(SessionFactory):
    # No-show marking is now handled exclusively by run_access_window_monitoring
    # (app/automation/jobs.py) using the Issue #31 rule: no_show is set when
    # now_utc > start_at + 5 minutes (grace period).  This function is retained
    # as a registered scheduler job stub to avoid breaking existing job
    # registrations; it performs no database writes.
    pass
