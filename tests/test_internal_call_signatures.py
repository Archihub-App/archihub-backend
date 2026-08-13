"""Internal helpers are called with keyword arguments they actually accept.

Sibling to `test_internal_imports_resolve.py`, catching the failure one step
further in: the name resolves, the module is right, and the call is still wrong.

`core.responses.file_response` takes ``download_name``. **All four plugin
download routes passed ``filename``** — inventoryMaker's task result and its
public export, massiveUpdater's result, liquidText's result. Every one raised
``TypeError: file_response() got an unexpected keyword argument 'filename'`` on
its first real request. The core call sites had it right, so the mistake was
invisible to anything that looked at `file_response` itself; it lived entirely in
the callers, in routes whose tests assert the *guard* rather than the download.

Scope and its reasons:

* Only functions defined **exactly once** across `archihub/` are checked. A name
  defined twice cannot be resolved from an AST without real name resolution, and
  a false positive here is worse than a missed one.
* Only direct calls (``helper(...)``), never attribute calls
  (``obj.helper(...)``) — the receiver's type is unknown.
* A function taking ``**kwargs`` accepts anything, so it is skipped.

That leaves the case that actually bites: a module-level helper, imported by
name, called with a keyword its author renamed or never had.
"""

from __future__ import annotations

import ast
import pathlib
from collections import defaultdict

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "archihub"


def _accepted_keywords(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str] | None:
    """Keyword names this definition accepts, or None if it accepts anything."""
    if node.args.kwarg is not None:
        return None
    args = node.args
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)} - {"self", "cls"}


def _definitions() -> dict[str, set[str] | None]:
    """Function name -> accepted keywords, for names defined exactly once."""
    seen: dict[str, list[set[str] | None]] = defaultdict(list)

    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen[node.name].append(_accepted_keywords(node))

    return {name: entries[0] for name, entries in seen.items() if len(entries) == 1}


DEFINITIONS = _definitions()


def _suspect_calls() -> list[tuple[str, int, str, str]]:
    """Every (file, line, function, keyword) that the definition does not accept."""
    problems: list[tuple[str, int, str, str]] = []

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue

            accepted = DEFINITIONS.get(node.func.id, "unknown")
            if accepted == "unknown" or accepted is None:
                continue

            for keyword in node.keywords:
                if keyword.arg is None:  # **spread, contents unknowable here
                    continue
                if keyword.arg not in accepted:
                    problems.append(
                        (
                            str(path.relative_to(PACKAGE_ROOT.parent)),
                            node.lineno,
                            node.func.id,
                            keyword.arg,
                        )
                    )

    return problems


def test_the_scan_has_something_to_resolve_against():
    """A guard that silently checks nothing is worse than no guard."""
    assert len(DEFINITIONS) > 200, f"only resolved {len(DEFINITIONS)} unique definitions"


def test_no_internal_call_passes_an_unaccepted_keyword():
    problems = _suspect_calls()

    assert not problems, "Calls passing a keyword the definition does not accept:\n" + "\n".join(
        f"  {src}:{line} -> {func}({kw}=...)" for src, line, func, kw in problems
    )


def test_the_guard_notices_a_wrong_keyword():
    """Proves the scan works, using the real signature that broke the downloads."""
    source = "def file_response(path, *, download_name=None): ...\nfile_response(p, filename='x')\n"
    tree = ast.parse(source)

    definition = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    accepted = _accepted_keywords(definition)

    assert "download_name" in accepted
    assert "filename" not in accepted
