"""Every internal import names something that exists.

`archihub` uses function-local imports heavily and for good reasons — breaking
cycles between domains, and keeping optional subsystems out of a process that
does not need them. The cost is that a wrong module path is not an error until
the line runs, and a line inside a rarely-taken branch may not run for months.

That is not hypothetical. `inventoryMaker._may_export` imported `type_roles`
from `archihub.core.roles`, where it does not live (it is in
`archihub.api.resources.hierarchy`). The whole bulk-export route 500'd with an
`ImportError` on its first real request — past the role check, past the tests,
straight to an operator clicking a button.

This walks the source rather than the running program, so an import inside an
`except` branch or a route nobody calls is checked exactly like any other.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "archihub"


def _internal_imports() -> list[tuple[str, int, str, str]]:
    """Every `from archihub... import name`, as (file, line, module, name)."""
    found: list[tuple[str, int, str, str]] = []

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            # `level > 0` is a relative import; resolving it needs the package
            # context and the codebase does not use them.
            if node.level or not node.module or not node.module.startswith("archihub"):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                found.append(
                    (str(path.relative_to(PACKAGE_ROOT.parent)), node.lineno, node.module, alias.name)
                )

    return found


IMPORTS = _internal_imports()


def test_the_scan_found_something_to_check():
    """A guard that silently checks nothing is worse than no guard."""
    assert len(IMPORTS) > 100, f"only found {len(IMPORTS)} internal imports; the scan is broken"


@pytest.mark.parametrize(
    ("source", "line", "module", "name"),
    IMPORTS,
    ids=[f"{s}:{ln}:{m}.{n}" for s, ln, m, n in IMPORTS],
)
def test_internal_import_resolves(source, line, module, name):
    try:
        imported = importlib.import_module(module)
    except Exception as exc:  # pragma: no cover - the failure message is the point
        pytest.fail(f"{source}:{line} imports from {module!r}, which cannot be imported: {exc}")

    if hasattr(imported, name):
        return

    # `from package import submodule` is legal and the attribute only appears
    # once the submodule itself has been imported.
    try:
        importlib.import_module(f"{module}.{name}")
    except ModuleNotFoundError:
        pytest.fail(
            f"{source}:{line} imports {name!r} from {module!r}, "
            f"which defines no such name and has no such submodule."
        )
