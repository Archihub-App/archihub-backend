"""Reading a client's page number.

A page number arrives in a request body, so it may be anything JSON can carry.
It becomes a ``skip``, and a value that is not a small non-negative integer must
be refused before it reaches a query: ``int("x")`` raises, and a negative
``skip`` is a database error.
"""

from __future__ import annotations

#: Highest page any listing accepts.
MAX_PAGE = 100_000


def page_number(value: object) -> int | None:
    """The page ``value`` names, ``0`` when absent, or ``None`` when unusable."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 0 <= value <= MAX_PAGE:
        return None
    return value
