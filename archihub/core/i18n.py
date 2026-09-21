"""Translations.

The active language is an instance-wide setting stored in MongoDB (the
``user_languages`` entry of the ``user_management`` system document), not a
per-user or per-request choice. Two properties follow from that:

* The locale is **global to the instance**, not per-user or per-request - it is
  a single system setting. So Celery task bodies can resolve it exactly the same
  way route handlers do, and nothing needs to be threaded through as an argument.
* The catalogs are ordinary GNU gettext ``.mo`` files. Stdlib ``gettext`` reads
  them directly, and its language expansion maps ``es`` onto the on-disk
  ``es_ES/LC_MESSAGES/messages.mo``, so no file has to move and
  ``compile_translations.sh`` keeps working unchanged.

Keyword arguments are interpolated into the translated message, in either
placeholder style - see ``interpolate``.
"""

from __future__ import annotations

import gettext as _gettext
import logging
import os
import re as _re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DOMAIN = "messages"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("es", "en")

# Entry id inside the `user_management` settings document that holds the
# instance locale. Confirmed against a live database.
LOCALE_SETTING_ID = "user_languages"

# How long a resolved locale is reused before Mongo is consulted again: short
# enough that a language change shows almost at once, long enough to keep the
# read off the hot path.
_LOCALE_TTL_SECONDS = 30

_locale_cache: tuple[str, float] | None = None
_translations_cache: dict[tuple[str, tuple[str, ...]], _gettext.NullTranslations] = {}


def _repo_root() -> Path:
    # archihub/core/i18n.py -> archihub/core -> archihub -> <repo root>
    return Path(__file__).resolve().parents[2]


def translation_directories() -> list[Path]:
    """The application catalog, plus one per plugin that ships translations.

    Order is significant: the first readable catalog is primary and the rest are
    registered as fallbacks, so a plugin may add msgids but never redefine one
    the application already translates.

    A plugin's catalog is found under its own directory, which is what makes a
    plugin installable by copying it in - its translations arrive with it and
    need no change here.
    """
    root = _repo_root()
    directories = [root / "archihub" / "translations"]

    plugins_root = root / "archihub" / "plugins"
    if plugins_root.is_dir():
        for plugin_dir in sorted(plugins_root.iterdir()):
            candidate = plugin_dir / "translations"
            if candidate.is_dir():
                directories.append(candidate)

    return [d for d in directories if d.is_dir()]


def get_locale() -> str:
    """Resolve the instance-wide locale from the ``system`` collection.

    Falls back to ``DEFAULT_LOCALE`` when the setting is missing or malformed.
    The entry is looked up by id, with the positional read only as a fallback, so
    a reordered settings document never turns into a 500.
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

        # Looked up by id; the positional read is only a last-resort fallback for
        # a document whose entries carry no ids.
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


#: ``%(name)s`` - the printf placeholder style, used by many msgids in the catalogue.
_PRINTF_PLACEHOLDER = _re.compile(r"%\(\w+\)[sdifr]")


def interpolate(translated: str, variables: dict) -> str:
    """Fill a translated string's placeholders, in whichever style it uses.

    BOTH STYLES ARE IN USE: ``"Indexing finished for %(count)s resources"`` and
    ``"{field} is missing"``. Existing msgids are reused rather than duplicated,
    so both reach this function; a message filled in the wrong style would show
    its placeholder to the operator with no error anywhere.
    """
    if not variables:
        return translated
    try:
        if _PRINTF_PLACEHOLDER.search(translated):
            return translated % variables
        return translated.format(**variables)
    except (KeyError, IndexError, ValueError, TypeError):
        logger.warning("Bad interpolation for message %r", translated)
        return translated


def gettext(message: str, **variables: object) -> str:
    """Translate ``message`` into the instance locale.

    Keyword arguments are interpolated in whichever placeholder style the
    message uses - see ``interpolate``.
    """
    return interpolate(_get_translations(get_locale()).gettext(message), variables)


# The conventional gettext alias.
_ = gettext


def ngettext(singular: str, plural: str, n: int, **variables: object) -> str:
    translated = _get_translations(get_locale()).ngettext(singular, plural, n)
    return interpolate(translated, {**variables, "n": n})


if os.environ.get("ARCHIHUB_I18N_DEBUG"):  # pragma: no cover - manual diagnostics
    logger.info("Translation directories: %s", translation_directories())
