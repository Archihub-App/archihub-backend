"""Every timestamp the application records is UTC (``archihub/core/clock.py``).

The source guard reads every module under ``archihub/`` - installed plugins
included - for the calls that answer in the host's timezone. Test code is not
scanned: it may build whatever datetimes it needs.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import pytest

from archihub.core import clock

ROOT = Path(__file__).resolve().parents[1] / "archihub"

UTC = datetime.timezone.utc

# Methods of `datetime`/`date` that read the host's timezone, or produce a
# naive value, whatever their arguments.
_ALWAYS_NAIVE = {"utcnow", "today", "utcfromtimestamp"}
# Methods that do so unless given a timezone: the number is how many positional
# arguments it takes for one of them to be the timezone.
_NAIVE_WITHOUT_TZ = {"now": 1, "fromtimestamp": 2}
_RECEIVERS = {"datetime", "date"}


def _receiver_name(node: ast.expr) -> str | None:
    """``datetime`` for ``datetime.now`` and ``datetime.datetime.now``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _naive_calls(source: str) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = node.func.attr
        if _receiver_name(node.func.value) not in _RECEIVERS:
            continue
        if name in _ALWAYS_NAIVE:
            found.append((node.lineno, name))
        elif name in _NAIVE_WITHOUT_TZ:
            positional_tz = len(node.args) >= _NAIVE_WITHOUT_TZ[name]
            if not positional_tz and not any(k.arg == "tz" for k in node.keywords):
                found.append((node.lineno, name))
    return found


def _modules():
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.relative_to(ROOT).parts or "__pycache__" in path.parts:
            continue
        yield path


def test_no_module_reads_the_host_clock():
    offenders = [
        f"{path.relative_to(ROOT.parent)}:{line} {name}()"
        for path in _modules()
        for line, name in _naive_calls(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "Use archihub.core.clock.utcnow() - these read the host's timezone:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "source, flagged",
    [
        ("import datetime\ndatetime.datetime.now()", True),
        ("from datetime import datetime\ndatetime.now()", True),
        ("from datetime import datetime\ndatetime.utcnow()", True),
        ("from datetime import date\ndate.today()", True),
        ("import datetime\ndatetime.datetime.fromtimestamp(0)", True),
        ("from datetime import datetime, timezone\ndatetime.now(timezone.utc)", False),
        ("from datetime import datetime, timezone\ndatetime.now(tz=timezone.utc)", False),
        ("import datetime\ndatetime.datetime.fromtimestamp(0, datetime.timezone.utc)", False),
        ("clock.now()", False),
    ],
)
def test_the_guard_recognises_host_clock_calls(source, flagged):
    assert bool(_naive_calls(source)) is flagged


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------


def test_utcnow_is_aware_and_utc():
    now = clock.utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.timedelta(0)


def test_a_naive_value_is_read_as_utc():
    """MongoDB returns stored instants naive, in UTC."""
    stored = datetime.datetime(2026, 9, 23, 14, 8, 49)
    assert clock.as_utc(stored) == datetime.datetime(2026, 9, 23, 14, 8, 49, tzinfo=UTC)


def test_an_aware_value_is_converted_to_utc():
    bogota = datetime.timezone(datetime.timedelta(hours=-5))
    local = datetime.datetime(2026, 9, 23, 9, 8, 49, tzinfo=bogota)
    assert clock.as_utc(local) == datetime.datetime(2026, 9, 23, 14, 8, 49, tzinfo=UTC)


def test_isoformat_carries_the_offset():
    """Without an offset a browser reads the value as its own local time."""
    stored = datetime.datetime(2026, 9, 23, 14, 8, 49)
    assert clock.isoformat_utc(stored) == "2026-09-23T14:08:49+00:00"


# ---------------------------------------------------------------------------
# Comparisons against stored (naive) values
# ---------------------------------------------------------------------------


def test_an_api_key_expiry_read_back_naive_is_compared_in_utc(monkeypatch):
    """A stored expiry comes back naive; comparing it with an aware now must
    neither raise nor shift by the host's offset."""
    from archihub.core.security import api_keys

    fixed = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(api_keys, "utcnow", lambda: fixed)

    record = {
        "key_id": "k",
        "secret_hash": api_keys.hash_secret("s"),
        "user": "u",
        "scope": "public",
        "revoked_at": None,
        "last_used_at": datetime.datetime(2026, 9, 23, 11, 30),
    }

    def resolve(expires_at):
        found = dict(record, expires_at=expires_at)
        monkeypatch.setattr(api_keys, "_mongo", lambda: _OneRecord(found))
        return api_keys.verify_key(api_keys.format_key("k", "s"))

    assert resolve(datetime.datetime(2026, 9, 23, 12, 30)) is not None
    assert resolve(datetime.datetime(2026, 9, 23, 11, 59)) is None


class _OneRecord:
    def __init__(self, record):
        self.record = record

    def get_record(self, collection, filters, fields=None):
        return self.record

    def update_record(self, *args, **kwargs):
        return None


def test_the_weekly_quota_week_is_read_in_utc(monkeypatch):
    from archihub.api.users import services

    monday = datetime.datetime(2026, 9, 21, 0, 30, tzinfo=UTC)
    monkeypatch.setattr(services, "utcnow", lambda: monday)
    # Sunday 23:00 UTC is the previous ISO week; stored naive, as read back.
    assert services._is_date_in_current_week(datetime.datetime(2026, 9, 20, 23, 0)) is False
    assert services._is_date_in_current_week(datetime.datetime(2026, 9, 21, 0, 10)) is True


def test_a_since_filter_is_converted_to_naive_utc():
    from archihub.api.logs.recent import parse_since

    assert parse_since("2026-09-23T09:00:00-05:00") == datetime.datetime(2026, 9, 23, 14, 0)
    assert parse_since("2026-09-23T14:00:00Z") == datetime.datetime(2026, 9, 23, 14, 0)
    assert parse_since("2026-09-23T14:00:00") == datetime.datetime(2026, 9, 23, 14, 0)
