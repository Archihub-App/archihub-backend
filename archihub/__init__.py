"""ArchiHUB backend (FastAPI).

This package is the FastAPI rewrite of the legacy Flask application that still
lives in ``app/``. The two coexist deliberately during the migration: the old
stack stays runnable and untouched so it can serve as the reference
implementation for ``tools/diff_harness.py``, which compares old and new
responses field-by-field.

The new code does NOT live under ``app/`` because ``app/__init__.py`` builds
and boots the whole Flask application at import time (``app = create_app()`` at
module scope, preceded by a torch import and monkeypatch). Any
``from app.core... import ...`` would therefore drag the entire Flask stack -
Mongo reads, SkillManager, plugin registration - into the FastAPI process.

At the Phase 7 cutover, ``app/`` is deleted and this package remains.

ARCHITECTURE NOTE - this stack is intentionally synchronous.
Route handlers are declared with plain ``def`` (not ``async def``), which makes
Starlette run them in a worker threadpool. That is a deliberate decision, not an
oversight: HTTP routes and Celery tasks call the *same* service functions and
share the *same* client singletons, so a split async-web/sync-worker design
would require every service module to bind its client to two different types at
once. See PLAN_FASTAPI.md decision 6.
"""

__version__ = "1.4.1 beta"
