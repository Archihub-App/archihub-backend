"""A plugin declares what it needs, and the declaration is machine-checkable.

A plugin is a directory somebody copies in, so the build has to be able to read
its requirements without a person reading them first. Two manifests:

``requirements.txt``  Python packages. Checked against the plugin's imports by
                      ``test_declared_dependencies.py``.
``packages.txt``      SYSTEM packages, installed with apt.

**``packages.txt`` exists because authors were already declaring these - in
prose.** The convention was a comment at the top of ``requirements.txt``
("install tesseract-ocr"), which the build's merge step then deleted, because
it stripped comment lines. The result was a plugin that installed cleanly and
failed at its first task on a binary nothing had installed.

**The name pattern is a security boundary, not tidiness.** Each line is handed
to ``apt-get install`` running as root during the build, and the file comes from
a third-party directory. Anything that is not plainly a package name is refused
rather than escaped, so an entry like ``--allow-downgrades`` or ``a; curl …``
cannot reach the command at all.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "archihub" / "plugins"
INSTALLER = REPO_ROOT / "scripts" / "install_plugin_deps.sh"

RESERVED = {"framework", "__pycache__"}

#: Must stay identical to PACKAGE_PATTERN in scripts/install_plugin_deps.sh.
PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")


def _plugins() -> list[pathlib.Path]:
    return sorted(
        d for d in PLUGIN_ROOT.iterdir()
        if d.is_dir() and d.name not in RESERVED and (d / "__init__.py").is_file()
    )


def _entries(manifest: pathlib.Path) -> list[str]:
    lines = []
    for raw in manifest.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def test_the_scan_found_plugins():
    """A guard that silently checks nothing is worse than no guard."""
    assert _plugins(), "no plugins found to check"


@pytest.mark.parametrize("plugin", _plugins(), ids=lambda p: p.name)
def test_every_declared_system_package_is_a_package_name(plugin):
    manifest = plugin / "packages.txt"
    if not manifest.is_file():
        pytest.skip(f"{plugin.name} declares no system packages")

    for entry in _entries(manifest):
        assert PACKAGE_NAME.match(entry), (
            f"{plugin.name}/packages.txt: {entry!r} is not an apt package name. "
            "Each line is passed to apt-get running as root at build time."
        )


@pytest.mark.parametrize("plugin", _plugins(), ids=lambda p: p.name)
def test_a_plugin_that_shells_out_declares_the_binary(plugin):
    """The gap this manifest closes, asserted rather than trusted.

    A plugin invoking a binary by name needs the package that provides it. The
    check is deliberately narrow - the binaries this project actually shells out
    to - because a general one would have to guess.
    """
    provided_by = {"ffmpeg": "ffmpeg", "libreoffice": "libreoffice", "soffice": "libreoffice"}

    source = "\n".join(
        path.read_text() for path in plugin.rglob("*.py")
    )
    needed = {
        provided_by[binary]
        for binary in provided_by
        if f'"{binary}"' in source or f"'{binary}'" in source
    }
    if not needed:
        pytest.skip(f"{plugin.name} shells out to nothing")

    manifest = plugin / "packages.txt"
    declared = set(_entries(manifest)) if manifest.is_file() else set()

    missing = needed - declared
    assert not missing, (
        f"{plugin.name} invokes a binary it does not declare: {sorted(missing)}. "
        f"Add it to archihub/plugins/{plugin.name}/packages.txt - the image the "
        "plugin is installed into may not already carry it."
    )


def test_the_installer_and_this_test_agree_on_the_pattern():
    """Two copies of a rule that can disagree are one rule nobody enforces."""
    source = INSTALLER.read_text()
    assert "PACKAGE_PATTERN='^[a-z0-9][a-z0-9+.-]*$'" in source, (
        "scripts/install_plugin_deps.sh's package pattern changed; update "
        "PACKAGE_NAME here to match."
    )


def test_the_installer_constrains_plugin_installs():
    """A plugin may add packages, never move one the backend pinned.

    Without `-c`, a plugin requirement pinning an older shared library silently
    downgrades it - including the versions raised deliberately as security
    floors.
    """
    source = INSTALLER.read_text()
    assert source.count('-c "$CONSTRAINTS"') >= 2, (
        "every pip install of plugin requirements must pass -c $CONSTRAINTS"
    )


def test_source_builds_are_installed_without_build_isolation():
    """PEP 517 isolates each build, so a setup.py importing a runtime dependency
    cannot see it however the requirements file is ordered. The installer defers
    VCS and archive requirements to a second pass against the resolved
    environment."""
    source = INSTALLER.read_text()
    assert "--no-build-isolation" in source
