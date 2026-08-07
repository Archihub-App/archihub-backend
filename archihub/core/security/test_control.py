"""Gate for the ``/health/test-control/*`` routes.

Port of ``app/utils/TestControlAuth.py``.

These routes wipe and reseed an entire instance, so the gate is deliberately
paranoid and its behaviour is a hard contract with ``ArchiHUBTestRunner``, which
distinguishes the three failure modes by status code during preflight:

    404 -> ARCHIHUB_TEST_MODE is not set on this instance
    403 -> the disposable-instance marker document is missing
    401 -> the shared secret is wrong or absent

**The order and the response bodies must not change.** The runner reports "test
mode isn't reaching the container" vs "the marker doc is missing" purely from
which of these it gets back.

The two-part gate (env var AND a database document) is intentional. The env var
alone persists in infrastructure config and is easy to leave switched on; the
marker document is never written by application code, only by hand. So restoring
a production Mongo dump into a container that happens to have
ARCHIHUB_TEST_MODE=true does not silently make that data resettable.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from archihub.core.settings import get_settings

TEST_MODE_MARKER_NAME = "test_mode_active"


def is_disposable_instance() -> bool:
    """True when the manually-inserted marker document is present.

    Insert it with::

        db.system.insertOne({name: 'test_mode_active', value: true})

    Nothing in the application ever creates this document - by design.
    """
    from archihub.infra.mongo import get_mongo

    marker = get_mongo().get_record("system", {"name": TEST_MODE_MARKER_NAME})
    return bool(marker and marker.get("value") is True)


def test_control_authenticate(
    x_archihub_test_secret: str | None = Header(default=None, alias="X-ArchiHUB-Test-Secret"),
) -> None:
    """FastAPI dependency replacing the ``@testControlAuthenticate`` decorator."""
    settings = get_settings()

    if not settings.archihub_test_mode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"msg": "Test-control API is not enabled on this instance"},
        )

    if not is_disposable_instance():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"msg": "This instance is not marked as a disposable test instance"},
        )

    secret = settings.test_secret_header_key
    # Constant-time compare, and an unset server-side secret can never match -
    # so forgetting to configure TEST_SECRET_HEADER_KEY fails closed.
    if (
        not x_archihub_test_secret
        or not secret
        or not hmac.compare_digest(x_archihub_test_secret, secret)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"msg": "Invalid or missing X-ArchiHUB-Test-Secret header"},
        )
