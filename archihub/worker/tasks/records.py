"""Periodic maintenance tasks for records."""

from __future__ import annotations

import logging

from celery import shared_task

from archihub.api.records import storage

logger = logging.getLogger(__name__)


@shared_task(name="records.cleanup_temporary_records")
def cleanup_temporary_records(max_age_seconds: int = 86400) -> int:
    """Purge temporary files older than max_age_seconds (default 1 day / 86400s)."""
    return storage.cleanup_temporary_records(max_age_seconds=max_age_seconds)
