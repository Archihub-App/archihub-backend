"""The one clock: every timestamp the application records is UTC.

``datetime.now()`` answers in whatever timezone the host is set to, so a value
written with it means something different on every server - and MongoDB, which
stores a naive datetime as if it were UTC, cannot tell. Every "now" therefore
comes from :func:`utcnow`, and the browser converts to the viewer's timezone
when it displays a value. ``tests/test_clock.py`` holds the source to that.

Values read back from MongoDB are naive, in UTC. :func:`as_utc` makes one
comparable with :func:`utcnow`, and :func:`isoformat_utc` writes one with its
offset, so a client never has to guess the timezone.

Calendar dates entered as metadata (a ``simple-date`` field) are not
timestamps and do not go through here.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """The current moment, timezone-aware, in UTC."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime.

    A naive value is taken to be UTC already, which is what MongoDB returns.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    """ISO 8601 with an explicit ``+00:00``: unambiguous to any client."""
    return as_utc(value).isoformat()
