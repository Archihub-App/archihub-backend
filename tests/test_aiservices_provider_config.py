"""Provider configuration: what a response reveals, and where a provider may point.

Addresses are resolved through a stub, so these tests never touch the network.
"""

from __future__ import annotations

import socket

import pytest
from bson.objectid import ObjectId

from archihub.api.aiservices import providers

PUBLIC = "93.184.216.34"

RESOLVES = {
    "localhost": "127.0.0.1",
    "api.example.test": PUBLIC,
    "intranet.example.test": "10.0.0.5",
    "metadata.example.test": "169.254.169.254",
}


def fake_getaddrinfo(host, port, *args, **kwargs):
    address = RESOLVES.get(host, host)
    try:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        socket.inet_pton(family, address)
    except OSError:
        raise socket.gaierror("unknown host") from None
    return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]


class FakeMongo:
    def __init__(self):
        self.rows: dict = {}

    def get_record(self, collection, filters, fields=None):
        if "_id" in filters:
            return self.rows.get(filters["_id"])
        return next((r for r in self.rows.values() if r.get("name") == filters.get("name")), None)

    def insert_record(self, collection, payload):
        _id = ObjectId()
        self.rows[_id] = {"_id": _id, **payload}

        class Result:
            inserted_id = _id

        return Result()

    def update_record(self, collection, filters, payload):
        self.rows[filters["_id"]].update(payload)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(providers, "_mongo", lambda: fake)
    monkeypatch.setattr(providers, "_audit", lambda *args: None)
    monkeypatch.setattr(providers.catalogue, "clear_cache", lambda *args: None)
    monkeypatch.setattr(providers.socket, "getaddrinfo", fake_getaddrinfo)
    return fake


def stored(mongo, provider_id):
    return mongo.rows[ObjectId(provider_id)]


def create(body, *, is_admin=False):
    body = {"name": "p", "dialect": "openai-compatible", **body}
    return providers.create(body, "u", is_admin=is_admin)


# ---------------------------------------------------------------------------
# What a response reveals
# ---------------------------------------------------------------------------


def test_header_values_are_never_returned():
    shown = providers.present({"headers": {"api-key": "secret", "X-Org": "acme"}})

    assert shown["headers"] == {
        "api-key": providers.MASKED_HEADER_VALUE,
        "X-Org": providers.MASKED_HEADER_VALUE,
    }


@pytest.mark.parametrize(
    "stored_url, shown_url",
    [
        ("https://user:pass@api.example.test/v1", "https://api.example.test/v1"),
        ("https://user@api.example.test:8443/v1", "https://api.example.test:8443/v1"),
        ("http://u:p@[::1]:11434", "http://[::1]:11434"),
        ("https://api.example.test/v1", "https://api.example.test/v1"),
    ],
)
def test_credentials_in_a_stored_url_are_not_returned(stored_url, shown_url):
    assert providers.present({"base_url": stored_url})["base_url"] == shown_url


# ---------------------------------------------------------------------------
# Where a provider may point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://10.1.2.3/v1",
        "http://192.168.1.10/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://intranet.example.test/v1",
        "http://metadata.example.test/",
        "http://does-not-resolve.example.test/v1",
    ],
)
def test_only_an_administrator_may_use_an_internal_address(mongo, url):
    payload, status = create({"base_url": url})
    assert status == 400

    payload, status = create({"base_url": url}, is_admin=True)
    assert status == 201


def test_anyone_who_may_configure_providers_may_use_a_public_address(mongo):
    payload, status = create({"base_url": "https://api.example.test/v1"})

    assert status == 201


def test_a_credential_in_the_url_is_refused_even_for_an_administrator(mongo):
    payload, status = create({"base_url": "https://user:pass@api.example.test/v1"}, is_admin=True)

    assert status == 400


def test_a_non_administrator_cannot_move_a_provider_to_an_internal_address(mongo):
    provider_id = create({"base_url": "https://api.example.test/v1"})[0]["id"]

    payload, status = providers.update(provider_id, {"base_url": "http://10.0.0.9/"}, "u")

    assert status == 400
    assert stored(mongo, provider_id)["base_url"] == "https://api.example.test/v1"


def test_saving_an_unchanged_address_is_not_rechecked(mongo):
    """A non-administrator may edit other fields of a provider an administrator
    pointed at a local model server; the form sends the address back as shown."""
    _id = ObjectId()
    mongo.rows[_id] = {
        "_id": _id,
        "name": "local",
        "dialect": "ollama",
        "base_url": "http://u:p@127.0.0.1:11434",
    }
    shown = providers.present(mongo.rows[_id])["base_url"]

    payload, status = providers.update(str(_id), {"base_url": shown, "default_model": "m"}, "u")

    assert status == 200
    assert mongo.rows[_id]["base_url"] == "http://u:p@127.0.0.1:11434"
    assert mongo.rows[_id]["default_model"] == "m"


# ---------------------------------------------------------------------------
# Headers round-trip without their values
# ---------------------------------------------------------------------------


def test_a_masked_header_sent_back_keeps_its_stored_value(mongo):
    body = {"base_url": "https://api.example.test", "headers": {"api-key": "secret"}}
    provider_id = create(body)[0]["id"]
    shown = providers.present(stored(mongo, provider_id))["headers"]

    payload, status = providers.update(provider_id, {"headers": {**shown, "X-Org": "acme"}}, "u")

    assert status == 200
    assert stored(mongo, provider_id)["headers"] == {"api-key": "secret", "X-Org": "acme"}


def test_a_new_header_needs_a_real_value(mongo):
    provider_id = create({"base_url": "https://api.example.test"})[0]["id"]

    masked = {"api-key": providers.MASKED_HEADER_VALUE}

    payload, status = providers.update(provider_id, {"headers": masked}, "u")
    assert status == 400
    payload, status = create({"headers": masked, "base_url": "https://api.example.test"})
    assert status == 400


def test_a_header_left_out_is_removed(mongo):
    body = {"base_url": "https://api.example.test", "headers": {"a": "1", "b": "2"}}
    provider_id = create(body)[0]["id"]

    providers.update(provider_id, {"headers": {"a": providers.MASKED_HEADER_VALUE}}, "u")

    assert stored(mongo, provider_id)["headers"] == {"a": "1"}


@pytest.mark.parametrize(
    "headers", [["api-key"], "api-key: x", 5, {"api-key": 5}, {"api-key": None}]
)
def test_malformed_headers_are_refused_not_a_server_error(mongo, headers):
    payload, status = create({"base_url": "https://api.example.test", "headers": headers})
    assert status == 400

    provider_id = create({"base_url": "https://api.example.test"})[0]["id"]
    payload, status = providers.update(provider_id, {"headers": headers}, "u")
    assert status == 400
