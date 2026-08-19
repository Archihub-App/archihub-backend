"""Comments describe the code, not the history of the code.

Two rules, with different reasons.

**No finding identifiers.** This repository is public. `S14`, `F27` and their
neighbours index a private security review that is deliberately kept outside the
tree; naming one in a comment advertises that the review exists and hands a
reader its index. State the rule being enforced instead - a reader needs to know
what must hold, never what the ticket was called.

**No archaeology.** A comment explaining what some earlier implementation got
wrong is unreadable the moment that implementation is gone, and it is the wrong
thing to write down while other deployments still run it. Write the invariant:
"the caller's value indexes a fixed map, it is never joined into a path" says
everything the reader needs, and stays true forever.

Both rules apply to comments and docstrings only. Identifiers, string literals
and log messages are checked by the tests that cover their behaviour.
"""

from __future__ import annotations

import io
import pathlib
import re
import tokenize

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "archihub"

#: `S14`, `F27`, `P11`. Bounded to two digits so `F401` (a linter code) and
#: HTTP-ish tokens do not match.
FINDING_ID = re.compile(r"(?<![A-Za-z0-9_])[SFP]\d{1,2}(?![A-Za-z0-9_])")

#: Phrases that introduce a comparison with an implementation the reader cannot
#: open. "Legacy" is the load-bearing one; the rest are how it gets rephrased.
ARCHAEOLOGY = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"legacy|the original|the old (?:code|version|implementation|route|helper)|"
    r"this port|the port(?:'s)?|earlier revision|used to (?:be|do|return|check)|"
    r"before the (?:port|rewrite)|the previous (?:code|version|implementation)"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _prose(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every comment and docstring line in a file, with its line number."""
    lines: list[tuple[int, str]] = []
    source = path.read_text()
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)

    previous = tokenize.INDENT
    for token in tokens:
        if token.type == tokenize.COMMENT:
            lines.append((token.start[0], token.string))
        elif token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.ENCODING,
        ):
            # A string in statement position is a docstring.
            for offset, text in enumerate(token.string.splitlines()):
                lines.append((token.start[0] + offset, text))
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            previous = token.type

    return lines


def _hits(pattern: re.Pattern) -> list[str]:
    problems = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for lineno, text in _prose(path):
            if pattern.search(text):
                relative = path.relative_to(PACKAGE_ROOT.parent)
                problems.append(f"  {relative}:{lineno}  {text.strip()[:96]}")
    return problems


def test_the_scan_reads_real_prose():
    """A guard that silently checks nothing is worse than no guard."""
    total = sum(len(_prose(p)) for p in PACKAGE_ROOT.rglob("*.py"))
    assert total > 3000, f"only found {total} comment/docstring lines"


def test_no_comment_names_a_finding_identifier():
    problems = _hits(FINDING_ID)
    assert not problems, (
        f"{len(problems)} comment(s) name a private finding identifier. This "
        "repository is public; state the rule being enforced instead:\n"
        + "\n".join(problems)
    )


def test_no_comment_compares_against_an_implementation_that_is_gone():
    problems = _hits(ARCHAEOLOGY)
    assert not problems, (
        f"{len(problems)} comment(s) describe a previous implementation. Write "
        "the invariant the code enforces, which stays true and stays readable:\n"
        + "\n".join(problems)
    )
