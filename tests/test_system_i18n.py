"""System settings translation and locale switching tests."""

from __future__ import annotations

import copy
import pytest

from archihub.api.system import services
from archihub.api.system.i18n import (
    SETTINGS_ES_TO_EN,
    SETTINGS_EN_TO_ES,
    translate_system_settings,
    translate_text,
)
from archihub.core import i18n


class _FakeMongo:
    def __init__(self, records: dict | None = None):
        self.records: dict = records or {}
        self.collections: dict[str, list] = {}
        self.inserted: list = []
        self.updated: list = []

    def get_record(self, collection, filters, fields=None):
        if callable(self.records.get(collection)):
            return self.records[collection](filters)
        for r in self.collections.get(collection, []):
            if isinstance(filters, dict) and "name" in filters and r.get("name") == filters["name"]:
                return r
        return self.records.get(collection)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        records = self.collections.get(collection, [])
        if filters and "$nin" in filters.get("name", {}):
            excluded = set(filters["name"]["$nin"])
            records = [r for r in records if r.get("name") not in excluded]
        return list(records)

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))
        self.collections.setdefault(collection, []).append(record)

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))
        for r in self.collections.get(collection, []):
            if isinstance(filters, dict) and r.get("name") == filters.get("name"):
                r.update(update)


@pytest.fixture(autouse=True)
def _restore_real_locale(monkeypatch):
    """Restore the real locale resolver so these tests can test real translation."""
    monkeypatch.setattr(i18n, "get_locale", getattr(i18n, "_real_get_locale", i18n.get_locale))
    i18n.reset_locale_cache()
    yield
    i18n.reset_locale_cache()


@pytest.fixture
def system_mongo(monkeypatch):
    from archihub.api.system.default_settings import settings as defaults

    fake = _FakeMongo()
    fake.collections["system"] = copy.deepcopy(defaults)
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    return fake


def test_translate_text_bidirectional():
    # Spanish to English
    assert translate_text("Ajustes de usuario", "en") == "User settings"
    assert translate_text("Administración de la caché", "en") == "Cache management"
    assert translate_text("Limpiar caché", "en") == "Clear cache"
    assert translate_text("Español", "en") == "Spanish"
    assert translate_text("Inglés", "en") == "English"

    # English to Spanish
    assert translate_text("User settings", "es") == "Ajustes de usuario"
    assert translate_text("Cache management", "es") == "Administración de la caché"
    assert translate_text("Clear cache", "es") == "Limpiar caché"
    assert translate_text("Spanish", "es") == "Español"
    assert translate_text("English", "es") == "Inglés"

    # Unknown text preserved
    assert translate_text("Custom Plugin Setting", "en") == "Custom Plugin Setting"
    assert translate_text("Custom Plugin Setting", "es") == "Custom Plugin Setting"


def test_get_all_settings_translated_in_spanish(system_mongo):
    # Set language to Spanish
    user_mgmt = next(s for s in system_mongo.collections["system"] if s["name"] == "user_management")
    lang_entry = next(item for item in user_mgmt["data"] if item.get("id") == "user_languages")
    lang_entry["value"] = "es"
    i18n.reset_locale_cache()

    payload, status = services.get_all_settings()
    assert status == 200
    settings = payload["settings"]

    user_group = next(s for s in settings if s["name"] == "user_management")
    assert user_group["label"] == "Ajustes de usuario"
    lang_item = next(item for item in user_group["data"] if item.get("id") == "user_languages")
    assert lang_item["label"] in ("Idioma del sistema", "Idioma por defecto de los usuarios")
    assert lang_item["options"][0]["label"] == "Español"
    assert lang_item["options"][1]["label"] == "Inglés"

    cache_group = next(s for s in settings if s["name"] == "cache_management")
    assert cache_group["label"] == "Administración de la caché"
    cache_item = next(item for item in cache_group["data"] if item.get("id") == "cache_clean")
    assert cache_item["label"] == "Limpiar la caché de la aplicación"
    assert cache_item["btn_label"] == "Limpiar caché"


def test_get_all_settings_translated_in_english(system_mongo):
    # Set language to English
    user_mgmt = next(s for s in system_mongo.collections["system"] if s["name"] == "user_management")
    lang_entry = next(item for item in user_mgmt["data"] if item.get("id") == "user_languages")
    lang_entry["value"] = "en"
    i18n.reset_locale_cache()

    payload, status = services.get_all_settings()
    assert status == 200
    settings = payload["settings"]

    user_group = next(s for s in settings if s["name"] == "user_management")
    assert user_group["label"] == "User settings"
    lang_item = next(item for item in user_group["data"] if item.get("id") == "user_languages")
    assert lang_item["label"] in ("System language", "Default user language")
    assert lang_item["options"][0]["label"] == "Spanish"
    assert lang_item["options"][1]["label"] == "English"

    cache_group = next(s for s in settings if s["name"] == "cache_management")
    assert cache_group["label"] == "Cache management"
    cache_item = next(item for item in cache_group["data"] if item.get("id") == "cache_clean")
    assert cache_item["label"] == "Clear application cache"
    assert cache_item["btn_label"] == "Clear cache"

    post_types = next(s for s in settings if s["name"] == "post_types_settings")
    assert post_types["label"] == "Content type settings"

    search_group = next(s for s in settings if s["name"] == "index_management")
    assert search_group["label"] == "Search management"


def test_update_settings_switches_locale_and_translates_response(system_mongo):
    # Initial state is English
    user_mgmt = next(s for s in system_mongo.collections["system"] if s["name"] == "user_management")
    lang_entry = next(item for item in user_mgmt["data"] if item.get("id") == "user_languages")
    lang_entry["value"] = "en"
    i18n.reset_locale_cache()

    # Update to Spanish
    res, status = services.update_settings({"user_languages": "es"}, "admin")
    assert status == 200
    assert res["msg"] == "Ajustes actualizados con éxito"
    assert i18n.get_locale() == "es"

    # Update back to English
    res, status = services.update_settings({"user_languages": "en"}, "admin")
    assert status == 200
    assert res["msg"] == "Settings updated successfully"
    assert i18n.get_locale() == "en"


def test_update_settings_via_system_language_alias(system_mongo):
    i18n.reset_locale_cache()
    res, status = services.update_settings({"system_language": "es"}, "admin")
    assert status == 200
    assert res["msg"] == "Ajustes actualizados con éxito"
    assert i18n.get_locale() == "es"


def test_get_all_settings_ensures_language_option_if_missing(system_mongo):
    # Remove user_languages from user_management data
    user_mgmt = next(s for s in system_mongo.collections["system"] if s["name"] == "user_management")
    user_mgmt["data"] = [item for item in user_mgmt["data"] if item.get("id") != "user_languages"]

    payload, status = services.get_all_settings()
    assert status == 200
    user_group = next(s for s in payload["settings"] if s["name"] == "user_management")
    has_lang = any(item.get("id") == "user_languages" for item in user_group["data"])
    assert has_lang is True


def test_access_rights_metadata_translation(system_mongo, monkeypatch):
    ar = next(s for s in system_mongo.collections["system"] if s["name"] == "access_rights")
    ar["options"] = [{"id": "public", "term": "Público"}]
    monkeypatch.setattr("archihub.core.roles.access_rights_document", lambda: ar)

    # Spanish
    user_mgmt = next(s for s in system_mongo.collections["system"] if s["name"] == "user_management")
    lang_entry = next(item for item in user_mgmt["data"] if item.get("id") == "user_languages")
    lang_entry["value"] = "es"
    i18n.reset_locale_cache()

    doc, status = services.get_access_rights()
    assert status == 200
    assert doc["label"] == "Roles y acceso a la información"
    assert doc["description"] == "Niveles de acceso y configuración de roles"

    # English
    lang_entry["value"] = "en"
    i18n.reset_locale_cache()

    doc, status = services.get_access_rights()
    assert status == 200
    assert doc["label"] == "Roles and information access"
    assert doc["description"] == "Access levels and role configuration"


def test_access_rights_roles_button_translation(system_mongo):
    user_mgmt = next(s for s in system_mongo.collections["system"] if s["name"] == "user_management")
    lang_entry = next(item for item in user_mgmt["data"] if item.get("id") == "user_languages")

    # Spanish
    lang_entry["value"] = "es"
    i18n.reset_locale_cache()
    payload, status = services.get_all_settings()
    assert status == 200
    ar = next(s for s in payload["settings"] if s["name"] == "access_rights")
    roles_btn = next(item for item in ar["data"] if item.get("id") == "user_roles")
    assert roles_btn["label"] == "Roles de usuario"
    assert roles_btn["btn_label"] == "Gestionar permisos"
    assert roles_btn["value"] == "/admin/permissions"

    # English
    lang_entry["value"] = "en"
    i18n.reset_locale_cache()
    payload, status = services.get_all_settings()
    assert status == 200
    ar = next(s for s in payload["settings"] if s["name"] == "access_rights")
    roles_btn = next(item for item in ar["data"] if item.get("id") == "user_roles")
    assert roles_btn["label"] == "User roles"
    assert roles_btn["btn_label"] == "Manage permissions"
    assert roles_btn["value"] == "/admin/permissions"

