"""The success status a route ADVERTISES must be the one it SENDS.

Two rules, both learned from routes that were wrong in a way nothing could see.

**A route may not advertise a success code it never sends.** Nine create routes
documented ``201: {"description": "... created"}`` in their ``responses`` block
but never declared ``status_code=201``. FastAPI fills the gap with its own
default, so the published spec listed **both** 200 and 201 as success responses
for a single create - and the 200 was fiction. Nothing failed: the routes really
did answer 201, because their services returned it in the ``(payload, status)``
tuple, so every test that fired a request passed. Only the generated document
was wrong, which is exactly the artefact nobody re-reads and every third-party
integrator does.

**Deletes answer 200 with a body.** Some deletes here return data the caller can
act on - bulk results, remaining counts - so 204 cannot cover all of them. One
uniform rule beats splitting deletes into two classes by whether they happen to
have something to say, and a client that must ask "which kind of delete is
this?" has no rule at all.

Both rules are checked against the built application, not by reading source, so
a route added to any router is covered the moment it exists.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "true")


#: The external APIs are consumed by other organisations' scripts. Their status
#: codes are a frozen contract and are deliberately not held to the rules here.
FROZEN_PREFIXES = ("/adminApi", "/publicApi")


@pytest.fixture(scope="module")
def routes():
    from archihub.core.app_factory import create_app
    from archihub.core.routing import iter_api_routes

    app = create_app()
    return [
        (path, route)
        for path, route in iter_api_routes(app)
        if not path.startswith(FROZEN_PREFIXES)
    ]


def _declared_success(route) -> list[int]:
    responses = getattr(route, "responses", None) or {}
    return sorted(c for c in responses if isinstance(c, int) and 200 <= c < 300)


def _actual_success(route) -> int:
    return getattr(route, "status_code", None) or 200


def test_the_guard_sees_real_routes(routes):
    """A guard that silently checks nothing is worse than no guard."""
    assert len(routes) > 100, f"only found {len(routes)} routes"


def test_no_route_advertises_a_success_code_it_never_sends(routes):
    problems = []
    for path, route in routes:
        declared = _declared_success(route)
        if len(declared) != 1:
            # Several success codes documented is a deliberate statement that
            # the route really can answer more than one; nothing to check.
            continue
        actual = _actual_success(route)
        if actual != declared[0]:
            methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
            problems.append(
                f"  {methods:7} {path}  documents {declared[0]} but declares {actual}"
                f"  -> add status_code={declared[0]} to the decorator"
            )

    assert not problems, (
        f"{len(problems)} route(s) advertise a success status they do not send. "
        "FastAPI adds its own default alongside the documented code, so the "
        "published spec claims a response the route never produces:\n"
        + "\n".join(problems)
    )


def test_every_delete_answers_200(routes):
    problems = []
    for path, route in routes:
        if "DELETE" not in route.methods:
            continue
        actual = _actual_success(route)
        if actual != 200:
            problems.append(f"  DELETE {path} declares {actual}")

    assert not problems, (
        "Deletes answer 200 with a message. Some deletes return data a caller "
        "acts on, so 204 cannot cover all of them, and one uniform rule is "
        "worth more than a per-route decision:\n" + "\n".join(problems)
    )


def test_a_create_that_returns_201_declares_it(routes):
    """The half of the first rule that a missing ``responses`` block would hide.

    A route whose handler is named ``create`` and which documents nothing at all
    still has to say what it answers, or the spec shows a bare 200 by default.
    """
    problems = []
    for path, route in routes:
        if "POST" not in route.methods:
            continue
        name = getattr(route.endpoint, "__name__", "")
        if not name.startswith("create"):
            continue
        if _actual_success(route) != 201:
            problems.append(f"  POST {path} ({name}) declares {_actual_success(route)}")

    assert not problems, (
        "A route that creates an entity answers 201:\n" + "\n".join(problems)
    )
