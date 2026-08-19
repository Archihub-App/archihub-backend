"""Celery task bodies.

Dotted task names (``@shared_task(name='...')``) are stable identifiers: a
message already queued in Redis, and every row already in the `tasks`
collection, resolves by that string. Renaming one strands both.

IMPORTING THIS PACKAGE REGISTERS THE TASKS. ``celery_app`` autodiscovers
``archihub.worker.tasks``, which runs this file; a module not listed below is
not registered, and a worker receiving one of its messages answers
``NotRegistered`` and drops the job. Adding a task module means adding it here.

The imports are unused by name on purpose - the ``@shared_task`` decorator runs
at import time and that is the whole point of them.
"""

from archihub.worker.tasks import geometries as geometries  # noqa: F401
from archihub.worker.tasks import indexing as indexing  # noqa: F401
from archihub.worker.tasks import testcontrol as testcontrol  # noqa: F401

#: Every task name this backend registers. Asserted against Celery's own
#: registry in the test suite, so a module that stops being imported - or a name
#: that gets edited - fails the build rather than a queued job at runtime.
REGISTERED_TASK_NAMES = frozenset(
    {
        "system.regenerate_index",
        "system.index_resources",
        "system.index_resources_delete",
        "geosystem.regenerate_index_shapes",
        "geosystem.index_shapes",
        "testcontrol.reset",
    }
)
