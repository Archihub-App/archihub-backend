"""An unexpected exception never reaches the client as text.

A handler for ``Exception`` catches whatever went wrong: a database driver's
connection string, a path on the server's disk, a library's internals. Its text
is for the process log. What the client gets is a generic, translated message.
Handlers for a specific exception the code raises on purpose, with a message
written for the user, are not affected.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "archihub"


def _catches_everything(handler: ast.ExceptHandler) -> bool:
    return handler.type is None or (
        isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "BaseException")
    )


def _sends_text_of(node: ast.AST, name: str) -> bool:
    """Whether a return or yield under ``node`` puts ``str(name)`` in what it sends."""
    for child in ast.walk(node):
        if not isinstance(child, (ast.Return, ast.Yield)) or child.value is None:
            continue
        for inner in ast.walk(child.value):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id in ("str", "repr")
                and inner.args
                and isinstance(inner.args[0], ast.Name)
                and inner.args[0].id == name
            ):
                return True
    return False


def _offenders() -> list[str]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.relative_to(ROOT).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ExceptHandler) and node.name and _catches_everything(node):
                if any(_sends_text_of(statement, node.name) for statement in node.body):
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_no_catch_all_handler_answers_with_the_exception_text():
    offenders = _offenders()
    assert not offenders, (
        "these handlers send an unexpected exception's text to the client; log it "
        f"with logger.exception and answer a generic message instead: {offenders}"
    )


def test_the_scan_can_see_an_offender():
    """A scan that recognises nothing would pass the test above vacuously."""
    sample = ast.parse(
        "try:\n    pass\nexcept Exception as exc:\n    return {'msg': str(exc)}, 500\n"
    )
    handler = next(node for node in ast.walk(sample) if isinstance(node, ast.ExceptHandler))

    assert _sends_text_of(handler.body[0], "exc")
