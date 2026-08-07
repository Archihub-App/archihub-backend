"""Translations without Flask-Babel.

Verifies the two things the port has to get right: resolving the instance
locale out of the ``system`` collection, and finding the existing GNU gettext
catalogs on disk.
"""

from __future__ import annotations

import pytest

from archihub.core import i18n


@pytest.fixture(autouse=True)
def _clear_locale_cache():
    i18n.reset_locale_cache()
    yield
    i18n.reset_locale_cache()


class _FakeMongo:
    def __init__(self, record):
        self._record = record
        self.calls = 0

    def get_record(self, collection, filters, fields=None):
        self.calls += 1
        return self._record


def _patch_mongo(monkeypatch, record) -> _FakeMongo:
    fake = _FakeMongo(record)
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake


def test_locale_is_read_by_setting_id(monkeypatch):
    """Look the entry up by id, not by position.

    Against a live instance the entry is ``user_languages`` and happens to sit
    at index 2 - which is why the legacy positional read ``data[2]`` worked.
    Here it is deliberately moved so a positional read would pick the wrong one.
    """
    _patch_mongo(
        monkeypatch,
        {
            "data": [
                {"id": i18n.LOCALE_SETTING_ID, "value": "en"},
                {"id": "user_registration", "value": None},
                {"id": "user_password_recovery", "value": "es"},
            ]
        },
    )
    assert i18n.get_locale() == "en"


def test_locale_falls_back_to_legacy_position(monkeypatch):
    """An unrecognised id still resolves, matching legacy behaviour."""
    _patch_mongo(
        monkeypatch,
        {"data": [{"id": "a"}, {"id": "b"}, {"id": "renamed_someday", "value": "es"}]},
    )
    assert i18n.get_locale() == "es"


@pytest.mark.parametrize(
    "record",
    [None, {}, {"data": []}, {"data": [{"id": "x"}]}, {"data": [{"id": i18n.LOCALE_SETTING_ID, "value": "kl"}]}],
)
def test_malformed_settings_fall_back_to_default(monkeypatch, record):
    """Never raise on a missing, empty, short or unsupported-locale document.

    The legacy version indexed ``data[2]['value']`` unconditionally and would
    500 on any of these - including from inside a health check.
    """
    _patch_mongo(monkeypatch, record)
    assert i18n.get_locale() == i18n.DEFAULT_LOCALE


def test_unreachable_database_falls_back_to_default(monkeypatch):
    def _boom():
        raise ConnectionError("mongo is down")

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", _boom)
    assert i18n.get_locale() == i18n.DEFAULT_LOCALE


def test_locale_is_cached(monkeypatch):
    """The legacy selector hit Mongo on every request; a short TTL removes that."""
    fake = _patch_mongo(monkeypatch, {"data": [{"id": i18n.LOCALE_SETTING_ID, "value": "es"}]})
    for _ in range(5):
        i18n.get_locale()
    assert fake.calls == 1


def test_spanish_catalog_is_found_on_disk(monkeypatch):
    """stdlib gettext must resolve 'es' onto the existing es_ES/*.mo catalog.

    This is what makes dropping flask-babel safe: no catalog file has to move
    and compile_translations.sh keeps working unchanged.
    """
    _patch_mongo(monkeypatch, {"data": [{"id": i18n.LOCALE_SETTING_ID, "value": "es"}]})
    assert i18n.gettext("The token has expired") == "El token ha expirado"


def test_english_returns_the_source_string(monkeypatch):
    _patch_mongo(monkeypatch, {"data": [{"id": i18n.LOCALE_SETTING_ID, "value": "en"}]})
    assert i18n.gettext("The token has expired") == "The token has expired"


def test_interpolation_uses_str_format(monkeypatch):
    """Matches Flask-Babel 3+/4.x semantics, so call sites port verbatim."""
    _patch_mongo(monkeypatch, {"data": [{"id": i18n.LOCALE_SETTING_ID, "value": "en"}]})
    assert i18n.gettext("The field {label} is required", label="Title") == (
        "The field Title is required"
    )


def test_bad_interpolation_does_not_raise(monkeypatch):
    """A translator typo must not turn into a 500."""
    _patch_mongo(monkeypatch, {"data": [{"id": i18n.LOCALE_SETTING_ID, "value": "en"}]})
    assert i18n.gettext("Needs {missing}") == "Needs {missing}"


def test_translation_directories_include_core_catalog():
    directories = i18n.translation_directories()
    assert any(d.name == "translations" for d in directories)
    assert all(d.is_dir() for d in directories)
