"""The lockfile and the manifest must agree.

`pyproject.toml` declares the compatible RANGES; `uv.lock` pins exactly what a
build resolves within them, with hashes. The two drift the moment a dependency
is added or a bound is moved without re-locking, and the failure is quiet: the
build keeps working from the stale lock and the new dependency is simply absent.

These checks deliberately need no `uv` binary. `uv lock --check` is the
authoritative test and is the right thing to run in CI, but a guard that only
runs where a particular tool is installed is a guard that mostly does not run.
Reading both files answers the question that actually matters here - is every
declared dependency pinned, at a version the declaration permits.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LOCKFILE = ROOT / "uv.lock"


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


@pytest.fixture(scope="module")
def lock() -> dict:
    if not LOCKFILE.is_file():
        pytest.fail(
            "uv.lock is missing. Reproducible installs depend on it; "
            "regenerate it with `uv lock`."
        )
    return tomllib.loads(LOCKFILE.read_text())


@pytest.fixture(scope="module")
def locked_versions(lock) -> dict[str, str]:
    return {_normalise(p["name"]): p["version"] for p in lock["package"]}


def test_the_lockfile_covers_every_declared_dependency(manifest, locked_versions):
    """A dependency added to the manifest and never locked is absent from builds."""
    from packaging.requirements import Requirement

    missing = []
    for raw in manifest["project"]["dependencies"]:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        if _normalise(requirement.name) not in locked_versions:
            missing.append(requirement.name)

    assert missing == [], (
        "declared in pyproject.toml but absent from uv.lock - run `uv lock`: "
        + ", ".join(sorted(missing))
    )


def test_every_locked_version_satisfies_its_declared_range(manifest, locked_versions):
    """Otherwise the lock quietly overrides a bound the manifest states.

    The lower bounds are not stylistic - several are security floors, chosen so
    a resolve cannot pick a version carrying a known advisory.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    violations = []
    for raw in manifest["project"]["dependencies"]:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        locked = locked_versions.get(_normalise(requirement.name))
        if locked and not requirement.specifier.contains(Version(locked), prereleases=True):
            violations.append(f"{requirement.name}: locked {locked}, declared {requirement.specifier}")

    assert violations == [], "uv.lock contradicts pyproject.toml:\n  " + "\n  ".join(violations)


def test_the_lock_is_pinned_and_hashed(lock):
    """A lock without hashes is a version list, not a supply-chain control."""
    registry_packages = [
        p for p in lock["package"] if p.get("source", {}).get("registry")
    ]
    assert len(registry_packages) > 100, "the lock looks truncated"

    unhashed = [
        p["name"]
        for p in registry_packages
        if not any("hash" in entry for entry in p.get("wheels", []) + [p.get("sdist") or {}])
    ]
    assert unhashed == [], "locked without a hash: " + ", ".join(sorted(unhashed))


def test_the_lock_targets_the_python_the_project_supports(manifest, lock):
    declared = manifest["project"]["requires-python"].replace(" ", "")
    locked = lock["requires-python"].replace(" ", "")
    assert declared == locked, (
        f"pyproject requires-python is {declared!r} but uv.lock targets {locked!r} - "
        "re-lock so the resolution covers the interpreters this project claims to support"
    )


def test_the_project_version_matches(manifest, lock):
    """The lock records the project's own version; a stale one misreports a release."""
    project = next(
        (p for p in lock["package"] if _normalise(p["name"]) == _normalise(manifest["project"]["name"])),
        None,
    )
    assert project is not None, "the project itself is not in the lock"
    assert project["version"] == manifest["project"]["version"]
