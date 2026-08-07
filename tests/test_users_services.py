"""Auth-facing user helpers.

The headline case is ``test_has_role_returns_a_real_bool_for_unknown_user``.
The legacy tree carries two implementations of ``has_role``/``has_right``: the
correct one in ``app/api/users/services.py``, and a copy in
``app/utils/functions.py`` that returns ``jsonify({'msg': ...}), 400`` for an
unknown user. That is a non-empty tuple, so it is TRUTHY - and every caller
writes ``if not has_role(...): deny``. Against that copy, a nonexistent user
passes the check.

``app/api/records/services.py`` imports from the broken copy, so the whole
records domain runs on it today.
"""

from __future__ import annotations

import datetime

import pytest

from archihub.api.users import services
from archihub.core.errors import NotFoundError, RateLimitError


class FakeMongo:
    def __init__(self, records: dict | None = None):
        self.records = records or {}
        self.updates: list[tuple] = []

    def get_record(self, collection, filters, fields=None):
        return self.records.get(collection)

    def update_record(self, collection, filters, update):
        self.updates.append((collection, filters, update))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# has_role / has_right
# ---------------------------------------------------------------------------


def test_has_role_true_when_user_holds_it(mongo):
    mongo.records["users"] = {"roles": ["admin", "editor"]}
    assert services.has_role("alice", "admin") is True


def test_has_role_false_when_user_lacks_it(mongo):
    mongo.records["users"] = {"roles": ["editor"]}
    assert services.has_role("alice", "admin") is False


def test_has_role_returns_a_real_bool_for_unknown_user(mongo):
    """The bug fix. Must be exactly False - never a truthy tuple.

    `is False` rather than `== False` on purpose: a `(response, 400)` tuple
    would satisfy neither, but `assert not x` would also catch an empty tuple,
    which is not what we want to permit either.
    """
    mongo.records["users"] = None
    result = services.has_role("ghost", "admin")
    assert result is False
    assert not isinstance(result, tuple)


def test_has_right_returns_a_real_bool_for_unknown_user(mongo):
    mongo.records["users"] = None
    assert services.has_right("ghost", "publish") is False


def test_user_without_roles_field_is_denied(mongo):
    """A malformed document must not raise KeyError inside an auth check."""
    mongo.records["users"] = {}
    assert services.has_role("alice", "admin") is False
    assert services.has_right("alice", "publish") is False


# ---------------------------------------------------------------------------
# Scheduled-task pseudo-users
# ---------------------------------------------------------------------------


def test_configured_scheduler_user_is_authorised(mongo):
    """scheduleSystemTasks runs jobs as system_scheduler_<taskname>.

    Such a user has no `users` document, so authorisation comes from a matching
    scheduled task actually being configured.
    """
    mongo.records["system"] = {
        "plugins_settings": {
            "scheduleSystemTasks": {"schedule_tasks": [{"task": "filesProcessing.create_webfile"}]}
        }
    }
    assert services.has_role("system_scheduler_filesProcessing.create_webfile", "admin") is True


def test_unconfigured_scheduler_user_is_not_authorised(mongo):
    """The prefix alone must not grant anything - otherwise it is a bypass."""
    mongo.records["system"] = {"plugins_settings": {"scheduleSystemTasks": {"schedule_tasks": []}}}
    mongo.records["users"] = None
    assert services.has_role("system_scheduler_not.configured", "admin") is False


def test_scheduler_prefix_with_no_plugin_settings(mongo):
    mongo.records["system"] = {}
    mongo.records["users"] = None
    assert services.has_role("system_scheduler_anything", "admin") is False


# ---------------------------------------------------------------------------
# get_by_username
# ---------------------------------------------------------------------------


def test_get_by_username_returns_the_user(mongo):
    from bson import ObjectId

    mongo.records["users"] = {"_id": ObjectId(), "token": "t"}
    user = services.get_by_username("alice")
    assert isinstance(user["_id"], str)
    assert user["favorites"] == []


def test_get_by_username_raises_for_unknown_user(mongo):
    mongo.records["users"] = None
    with pytest.raises(NotFoundError):
        services.get_by_username("ghost")


# ---------------------------------------------------------------------------
# add_request (weekly quota)
# ---------------------------------------------------------------------------


def test_first_request_starts_the_counter(mongo):
    # A projected document always carries _id, so an existing user with no
    # request history is a non-empty dict - never {}.
    mongo.records["users"] = {"_id": "abc"}
    services.add_request("alice")
    _, _, update = mongo.updates[0]
    assert update["requests"] == 1


def test_add_request_raises_for_unknown_user(mongo):
    mongo.records["users"] = None
    with pytest.raises(NotFoundError):
        services.add_request("ghost")


def test_request_within_the_week_increments(mongo):
    mongo.records["users"] = {"_id": "abc", "requests": 5, "lastRequest": datetime.datetime.now()}
    services.add_request("alice")
    assert mongo.updates[0][2]["requests"] == 6


def test_counter_resets_in_a_new_week(mongo):
    mongo.records["users"] = {
        "requests": 1999,
        "lastRequest": datetime.datetime.now() - datetime.timedelta(days=30),
    }
    services.add_request("alice")
    assert mongo.updates[0][2]["requests"] == 1


def test_exhausted_quota_raises_rate_limit_error(mongo):
    mongo.records["users"] = {
        "requests": services.MAX_REQUESTS_PER_WEEK,
        "lastRequest": datetime.datetime.now(),
    }
    with pytest.raises(RateLimitError) as exc:
        services.add_request("alice")
    assert exc.value.status_code == 429


def test_corrupt_last_request_resets_rather_than_raising(mongo):
    """A non-datetime lastRequest must not 500 an authenticated request."""
    mongo.records["users"] = {"_id": "abc", "requests": 10, "lastRequest": "not-a-date"}
    services.add_request("alice")
    assert mongo.updates[0][2]["requests"] == 1
