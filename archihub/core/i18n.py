"""Translations, without Flask-Babel.

The legacy app configured Flask-Babel with a ``locale_selector`` that read the
active language from MongoDB on every request::

    def get_locale():
        user_management = mongodb.get_record('system', {'name': 'user_management'})
        return user_management['data'][2]['value']

Two properties of that make the port simple:

* The locale is **global to the instance**, not per-user or per-request - it is
  a single system setting. So Celery task bodies can resolve it exactly the same
  way route handlers do, and nothing needs to be threaded through as an argument.
  This is why removing Flask's app-context wrapper from Celery costs so little.
* The catalogs are ordinary GNU gettext ``.mo`` files. Stdlib ``gettext`` reads
  them directly, and its language expansion maps ``es`` onto the on-disk
  ``es_ES/LC_MESSAGES/messages.mo``, so no file has to move and
  ``compile_translations.sh`` keeps working unchanged.

Interpolation follows Flask-Babel 3+/4.x semantics - ``str.format`` with keyword
arguments, e.g. ``_('The field {label} is required', label=...)`` - so existing
call sites port verbatim.
"""

from __future__ import annotations

import gettext as _gettext
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DOMAIN = "messages"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("es", "en")

# Entry id inside the `user_management` settings document that holds the
# instance locale. Confirmed against a live database.
LOCALE_SETTING_ID = "user_languages"

# How long a resolved locale is reused before Mongo is consulted again. The
# legacy code hit Mongo once per request; a short TTL keeps behaviour
# indistinguishable in practice while removing that round-trip from the hot path.
_LOCALE_TTL_SECONDS = 30

_locale_cache: tuple[str, float] | None = None
_translations_cache: dict[tuple[str, tuple[str, ...]], _gettext.NullTranslations] = {}


def _repo_root() -> Path:
    # archihub/core/i18n.py -> archihub/core -> archihub -> <repo root>
    return Path(__file__).resolve().parents[2]


def translation_directories() -> list[Path]:
    """Core catalog, the port's own additions, plus one per plugin.

    Port of ``get_translation_directories()``. Order is significant: the first
    readable catalog is primary and the rest are registered as fallbacks, so a
    msgid the legacy catalog already translates keeps that translation.

    ``archihub/translations`` holds **only msgids the rewrite introduced**. It
    exists because ``app/`` is the reference implementation the diff harness
    compares against and nothing in the port writes to it - so new strings could
    not simply be appended to the legacy ``.po``. The two merge into one catalog
    when ``app/`` is deleted at Phase 7.
    """
    root = _repo_root()
    directories = [root / "app" / "translations", root / "archihub" / "translations"]

    plugins_root = root / "app" / "plugins"
    if plugins_root.is_dir():
        for plugin_dir in sorted(plugins_root.iterdir()):
            candidate = plugin_dir / "translations"
            if candidate.is_dir():
                directories.append(candidate)

    return [d for d in directories if d.is_dir()]


def get_locale() -> str:
    """Resolve the instance-wide locale from the ``system`` collection.

    Falls back to ``DEFAULT_LOCALE`` when the setting is missing or malformed.
    The legacy version indexed ``data[2]['value']`` directly and would raise on
    a differently-shaped document; this looks the entry up by id first and only
    then falls back to the positional read, so a health check never 500s because
    a settings document was reordered.
    """
    global _locale_cache

    now = time.monotonic()
    if _locale_cache is not None and now - _locale_cache[1] < _LOCALE_TTL_SECONDS:
        return _locale_cache[0]

    locale = DEFAULT_LOCALE
    try:
        from archihub.infra.mongo import get_mongo

        record = get_mongo().get_record("system", {"name": "user_management"})
        data = (record or {}).get("data") or []

        # The setting's id is 'user_languages' (verified against a live
        # instance). The legacy code reached for data[2] positionally, which
        # happens to be this entry today but breaks silently if the settings
        # document is ever reordered or extended - so look it up by id, and keep
        # the positional read only as a last-resort fallback.
        entry = next((item for item in data if item.get("id") == LOCALE_SETTING_ID), None)
        if entry is None and len(data) > 2:
            entry = data[2]

        value = (entry or {}).get("value")
        if isinstance(value, str) and value in SUPPORTED_LOCALES:
            locale = value
    except Exception:
        logger.debug("Could not resolve locale from settings; using %s", DEFAULT_LOCALE, exc_info=True)

    _locale_cache = (locale, now)
    return locale


def reset_locale_cache() -> None:
    """Force the next ``get_locale()`` to re-read Mongo (settings changed, tests)."""
    global _locale_cache
    _locale_cache = None


def _get_translations(locale: str) -> _gettext.NullTranslations:
    directories = tuple(str(d) for d in translation_directories())
    key = (locale, directories)

    cached = _translations_cache.get(key)
    if cached is not None:
        return cached

    merged: _gettext.NullTranslations | None = None
    for directory in directories:
        try:
            catalog = _gettext.translation(
                DOMAIN, directory, languages=[locale], fallback=True
            )
        except OSError:  # pragma: no cover - unreadable catalog
            continue
        if merged is None:
            merged = catalog
        elif hasattr(merged, "add_fallback"):
            merged.add_fallback(catalog)

    result = merged or _gettext.NullTranslations()
    _translations_cache[key] = result
    return result


def gettext(message: str, **variables: object) -> str:
    """Translate ``message`` into the instance locale.

    Mirrors ``flask_babel.gettext``: the msgid is the English source string, and
    keyword arguments are interpolated with ``str.format``.
    """
    translated = _get_translations(get_locale()).gettext(message)
    if variables:
        try:
            return translated.format(**variables)
        except (KeyError, IndexError):
            logger.warning("Bad interpolation for message %r", message)
            return translated
    return translated


# Conventional alias, matching `from flask_babel import gettext as _`.
_ = gettext


def ngettext(singular: str, plural: str, n: int, **variables: object) -> str:
    translated = _get_translations(get_locale()).ngettext(singular, plural, n)
    if variables:
        try:
            return translated.format(**variables, n=n)
        except (KeyError, IndexError):
            return translated
    return translated


if os.environ.get("ARCHIHUB_I18N_DEBUG"):  # pragma: no cover - manual diagnostics
    logger.info("Translation directories: %s", translation_directories())
