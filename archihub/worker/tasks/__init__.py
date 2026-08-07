"""Celery task bodies.

Task modules are added here in Phase 4, in the same order the domains are
ported. Dotted task names (``@shared_task(name='...')``) are preserved verbatim
from the legacy code so that tasks already queued in Redis at cutover, and the
history recorded in the ``tasks`` collection, keep resolving.
"""
