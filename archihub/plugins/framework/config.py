"""Per-plugin configuration.

A PLUGIN OWNS ITS OWN CONFIGURATION, exactly as it owns its Python
dependencies (``requirements.txt``) and its system packages
(``packages.txt``). A variable only one plugin reads is declared, documented
and supplied beside that plugin, and the backend knows nothing about it.

    archihub/plugins/<slug>/
        requirements.txt   Python packages
        packages.txt       system packages
        .env               this plugin's settings and credentials

The rule for deciding where a setting lives is one question: **does the backend
itself read it?** File-storage roots, the database, the broker and the signing
keys are the backend's, and a plugin needing one reads it from ``Settings``
rather than from the environment - re-reading a core variable is how a plugin
ends up with a different value from the application it is running inside.
Everything else belongs to the plugin.

WHAT THIS DELIBERATELY DOES NOT DO IS CALL ``load_dotenv()``. That mutates the
process-wide environment, so what a plugin can see depends on which *other*
plugins have been imported first - an ordering dependency between components
that are supposed to be independent. Values are read and returned; nothing is
written back into ``os.environ``.

THE PROCESS ENVIRONMENT WINS over the file - including an empty value, so a
deployment that lists a plugin variable at all hides the plugin's file. A
deployment can override one value without editing a file inside the plugin.

WHERE THE FILE IS READ FROM. Beside the plugin's package by default. An image
does not contain it (it holds credentials), so a container sets
``PLUGIN_CONFIG_PATH`` to a mounted directory laid out the same way -
``<PLUGIN_CONFIG_PATH>/<slug>/.env`` - and reads it from there. The code the
container runs is still the image's: that directory is only ever read for
``.env`` files, never imported.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: The file a plugin keeps its own settings in, beside its package.
ENV_FILENAME = ".env"

#: A directory holding ``<slug>/.env`` files, used instead of the plugins' own.
CONFIG_PATH_VARIABLE = "PLUGIN_CONFIG_PATH"


class MissingPluginSetting(RuntimeError):
    """A plugin needs a setting that this instance does not supply."""


def _plugin_directory(slug: str) -> Path:
    # archihub/plugins/framework/config.py -> archihub/plugins/<slug>
    return (Path(__file__).resolve().parent.parent / slug).resolve()


def env_file_path(slug: str) -> Path:
    """Where ``slug``'s ``.env`` is read from.

    Read from the process environment rather than the backend's settings, so
    that finding a plugin's file never depends on the backend's own
    configuration being complete.
    """
    root = os.environ.get(CONFIG_PATH_VARIABLE, "").strip()
    if root:
        return Path(root) / slug / ENV_FILENAME
    return _plugin_directory(slug) / ENV_FILENAME


def read_env_file(slug: str) -> dict[str, str]:
    """The plugin's own ``.env``, or an empty mapping if it has none.

    An unreadable file is reported and treated as absent: configuration that
    cannot be parsed must not stop a plugin whose other settings come from the
    environment, and the alternative - failing at import - takes the whole
    instance down for one malformed line.
    """
    path = env_file_path(slug)
    if not path.is_file():
        return {}

    try:
        from dotenv import dotenv_values

        return {key: value for key, value in dotenv_values(path).items() if value is not None}
    except Exception:
        logger.warning("Could not read %s; ignoring it", path, exc_info=True)
        return {}


def get(slug: str, name: str, default: str = "", *, required: bool = False) -> str:
    """One setting for one plugin.

    ``required=True`` raises rather than returning an empty string. That
    distinction is the point of the argument: an absent credential returned as
    ``""`` is passed to whatever needs it and comes back as an authentication
    failure from a remote service, naming nothing an operator can act on.
    """
    value = os.environ.get(name)
    if value is None:
        value = read_env_file(slug).get(name)

    value = (value or "").strip()
    if value:
        return value

    if required:
        raise MissingPluginSetting(
            f"The {slug} plugin needs {name}. Set it in the environment, "
            f"or in archihub/plugins/{slug}/{ENV_FILENAME}."
        )
    return default


def get_bool(slug: str, name: str, default: bool = False) -> bool:
    """A flag, accepting the shell spellings of "true" and nothing else.

    A bare truthiness check reads ``"0"`` and ``"false"`` as enabled, because
    both are non-empty strings.
    """
    raw = get(slug, name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
