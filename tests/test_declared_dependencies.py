"""Every third-party module the port imports is declared in pyproject.toml.

The guard exists because of a specific failure: `httpx` is imported at module
level by `archihub/api/aiservices/transport.py` - the single HTTP path every LLM
call goes through - but was declared only under `[optional-dependencies].dev`.
A production install (`uv sync --no-dev`, `pip install .`) therefore produced a
backend that raised `ModuleNotFoundError` inside `create_app()` and could not
start at all. Nothing caught it, because the dev environment had httpx installed
for pytest and the diff harness, so every test and every local run was fine.

That is the general shape: a manifest is never exercised by the code it
describes. The tests import from the *environment*, not from the declaration, so
the two can disagree indefinitely.

Import name to distribution name is resolved from installed metadata
(`packages_distributions`) rather than a hand-written table, so it cannot drift.
A module that cannot be resolved is REPORTED rather than skipped silently.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tomllib
from importlib.metadata import packages_distributions

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "archihub"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: Ours, not third-party.
LOCAL = {"archihub", "app", "tools", "tests", "main", "run", "config"}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared() -> set[str]:
    """Distribution names in [project].dependencies, normalised.

    The dev extra is deliberately NOT included: a runtime import satisfied only
    by a dev dependency is exactly the defect this file exists to catch.
    """
    data = tomllib.loads(PYPROJECT.read_text())
    names = set()
    for spec in data["project"]["dependencies"]:
        names.add(_normalise(re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0]))
    return names


def _imported() -> dict[str, set[str]]:
    """Third-party top-level module -> the files importing it."""
    found: dict[str, set[str]] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name and name not in sys.stdlib_module_names and name not in LOCAL:
                    found.setdefault(name, set()).add(str(path.relative_to(REPO_ROOT)))
    return found


DECLARED = _declared()
IMPORTED = _imported()
DISTRIBUTIONS = packages_distributions()


def test_the_scan_found_something_to_check():
    """A guard that silently checks nothing is worse than no guard."""
    assert len(IMPORTED) > 20, f"only found {len(IMPORTED)} third-party imports"
    assert len(DECLARED) > 20, f"only parsed {len(DECLARED)} declared dependencies"


def test_every_imported_module_resolves_to_a_distribution():
    """An unresolvable module means the map is incomplete, not that it is fine."""
    unresolved = sorted(name for name in IMPORTED if name not in DISTRIBUTIONS)
    assert not unresolved, (
        "These modules could not be mapped to an installed distribution, so their "
        "declaration cannot be checked:\n  " + "\n  ".join(unresolved)
    )


def test_every_runtime_import_is_a_runtime_dependency():
    problems = []
    for module, files in sorted(IMPORTED.items()):
        dists = {_normalise(d) for d in DISTRIBUTIONS.get(module, [])}
        if not dists or dists & DECLARED:
            continue
        where = sorted(files)[:3]
        problems.append(
            f"  {module} (from {', '.join(sorted(dists))}) imported by {', '.join(where)}"
        )

    assert not problems, (
        "Imported at runtime but not in [project].dependencies. A dev-only or "
        "transitive declaration is not enough - the app must start from a "
        "production install:\n" + "\n".join(problems)
    )


def test_httpx_specifically_is_a_runtime_dependency():
    """The original defect, named, because it is the one that stops the app booting."""
    assert "httpx" in DECLARED, (
        "httpx is imported at module level by archihub/api/aiservices/transport.py; "
        "create_app() raises ModuleNotFoundError without it."
    )


def test_the_dev_extra_does_not_carry_a_runtime_dependency():
    """Re-adding httpx to `dev` would satisfy every test while breaking the deploy."""
    data = tomllib.loads(PYPROJECT.read_text())
    dev = {
        _normalise(re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0])
        for spec in data["project"]["optional-dependencies"]["dev"]
    }
    runtime_modules = {
        _normalise(d)
        for module in IMPORTED
        for d in DISTRIBUTIONS.get(module, [])
    }
    leaked = sorted(dev & runtime_modules - DECLARED)
    assert not leaked, f"declared only under [dev] but imported at runtime: {leaked}"


# ---------------------------------------------------------------------------
# A refusal must carry a refusal status
# ---------------------------------------------------------------------------
#
# `JSONResponse` defaults to **200**. A handler that builds one by hand to
# refuse a caller therefore says "no" with a success code unless it passes
# `status_code` explicitly, and nothing about the call looks wrong.
#
# This is not hypothetical: removing that one keyword from
# `tasks/router.py::_authorize` - so one user's task list refuses another user
# with a 200 - passes the entire suite. Per-object decisions like that one
# ("may this caller read *these* tasks") cannot be plain dependencies, so they
# live in handler bodies where no dependency-level guard reaches them.


def _json_response_calls() -> list[tuple[str, int]]:
    import ast

    calls = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "JSONResponse"
            ):
                if not any(kw.arg == "status_code" for kw in node.keywords):
                    calls.append((str(path.relative_to(PACKAGE_ROOT.parent)), node.lineno))
    return calls


def test_every_hand_built_response_states_its_status():
    problems = _json_response_calls()
    assert not problems, (
        "JSONResponse defaults to 200, so a refusal built without an explicit "
        "status_code succeeds silently:\n"
        + "\n".join(f"  {path}:{line}" for path, line in problems)
    )
