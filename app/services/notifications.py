# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:15:54 2026

@author: NBoyd1
"""

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import Notification
from ..automation.job_utils import job_session

def queue_notification(db: Session, user_id: int, message: str):
    db.add(Notification(user_id=user_id, message=message))
    db.commit()

def process_notification_queue(SessionFactory):
    # Simulated email/SMS sender: prints messages to console and marks sent.
    # Uses job_session to ensure a clean session lifecycle with commit/rollback.
    with job_session(SessionFactory, job_name="notifications") as db:
        pending = db.execute(
            select(Notification).where(Notification.sent_at.is_(None)).order_by(Notification.created_at).limit(25)
        ).scalars().all()

        if not pending:
            return

        for n in pending:
            print(f"[Notification] to user_id={n.user_id}: {n.message}")
            n.sent_at = datetime.utcnow()
