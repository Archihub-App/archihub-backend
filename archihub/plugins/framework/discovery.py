"""Plugin discovery: what is installed, and whether this backend can run it.

One implementation, shared by every process that needs the answer - the ASGI app
factory, the Celery worker and beat, and the system API. They must agree: a
worker that loads a plugin the web process refused runs tasks for routes that do
not exist.

THE PLUGIN SET IS READ FROM DISK, NEVER WRITTEN DOWN

Installing a plugin is documented as copying its directory into
``archihub/plugins/`` and rebuilding, so the directory is the source of truth.
A list of names in source could never include a plugin an operator installed
this morning, and :func:`list_installed_plugins` backs the only screen a plugin
can be activated from - so a name it does not know is a plugin nobody can
switch on.

WHAT IS REFUSED, AND WHY THE TEST IS STRUCTURAL

A plugin this backend can mount exposes ``build()``. Plugins written against
Flask's ``Blueprint`` do not, and importing one into an ASGI process either
fails opaquely or - worse - appears to succeed while the features it provides
are silently absent. So ``build()`` is the predicate: read statically here,
enforced for real by :func:`~archihub.plugins.framework.mounting.build_plugin`.
One fact, asked in two places, which is what stops the plugins screen accepting
something the next startup cannot load.

A LISTING MUST NOT EXECUTE THE THING IT IS LISTING

:func:`read_manifest` parses ``__init__.py`` with :mod:`ast` and never imports
it. A plugin directory is third-party content that an operator copied in, and
listing plugins is a read - rendering an administration screen must not run code
from every directory that happens to be present, activated or not. The
consequence to keep in mind: everything read that way is what a plugin
*declares*, not what it does.
"""

from __future__ import annotations

import ast
import importlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

PLUGIN_PACKAGE = "archihub.plugins"
#: The directory the packages live in - this file is ``<root>/framework/discovery.py``.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

#: Directories under the plugin root that are not plugins.
RESERVED_DIRECTORIES = frozenset({"framework", "__pycache__"})

#: The function a plugin package must expose for this backend to build it.
PLUGIN_FACTORY = "build"

#: The metadata dictionary a plugin package must declare.
PLUGIN_MANIFEST = "plugin_info"

#: Ceiling on the ``__init__.py`` this module will parse. A plugin directory is
#: third-party content and this parse happens on an admin request, so the work
#: it can be made to do is bounded. Well past any real plugin - the largest in
#: the tree is ~30 KB.
MANIFEST_BYTE_LIMIT = 2_000_000

# A plugin slug is a directory name: letters, digits, underscore, hyphen. No
# dots (which would traverse packages) and no path separators.
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PluginDiscoveryError(RuntimeError):
    """Raised when a plugin cannot be located or loaded."""


class IncompatiblePluginError(RuntimeError):
    """Raised at startup when an active plugin cannot be mounted here."""


@dataclass(frozen=True)
class PluginManifest:
    """What a plugin declares about itself, read without importing it.

    ``mountable`` answers "can this backend build it", and ``problem`` says why
    not. They are reported rather than used to hide the plugin: an operator who
    has installed something needs to see it and be told what is wrong with it,
    which is strictly more useful than its absence from a list.
    """

    slug: str
    info: dict
    installed: bool
    mountable: bool
    problem: str | None = None


def _validate_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not _SLUG_PATTERN.match(slug):
        raise PluginDiscoveryError(
            f"Invalid plugin slug {slug!r} in the active_plugins document. "
            "Slugs must match [A-Za-z0-9_-]+ - a value containing dots or slashes "
            "could be used to import arbitrary modules."
        )
    return slug


# ---------------------------------------------------------------------------
# What is installed
# ---------------------------------------------------------------------------


def list_installed_plugins() -> list[str]:
    """Every plugin package present in the plugins directory, sorted.

    A directory counts as a plugin when it holds an ``__init__.py``, which is
    also what makes it importable. Whether it can be *mounted* is a separate
    question - see :func:`read_manifest` - and is deliberately not decided here,
    so that an installed-but-unusable plugin is still visible to the operator
    who installed it.
    """
    try:
        entries = sorted(os.scandir(PLUGIN_ROOT), key=lambda entry: entry.name)
    except OSError:
        logger.exception("Could not read the plugin directory %s", PLUGIN_ROOT)
        return []

    slugs: list[str] = []
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in RESERVED_DIRECTORIES:
            continue
        if not entry.is_dir():
            continue
        if not _SLUG_PATTERN.match(name):
            # The name becomes an import path, so a directory that cannot be a
            # slug is skipped rather than sanitised into a different one.
            logger.warning("Ignoring plugin directory %r: not a valid plugin name", name)
            continue
        if not os.path.isfile(os.path.join(entry.path, "__init__.py")):
            continue
        slugs.append(name)
    return slugs


def is_installed(slug: str) -> bool:
    return read_manifest(slug).installed


def is_mountable(slug: str) -> bool:
    """Whether this backend can build the plugin, decided from what it declares."""
    return read_manifest(slug).mountable


# ---------------------------------------------------------------------------
# What a plugin declares - read statically
# ---------------------------------------------------------------------------


def read_manifest(slug: str) -> PluginManifest:
    """Parse a plugin's ``__init__.py`` and report what it declares.

    Never raises and never imports. Every failure - a slug that is not a name, a
    directory that is not there, a file that will not parse - comes back as a
    manifest carrying ``problem``, because each of them is something an operator
    needs to read on the plugins screen rather than a stack trace in a log.
    """
    try:
        _validate_slug(slug)
    except PluginDiscoveryError as exc:
        return PluginManifest(str(slug), {}, installed=False, mountable=False, problem=str(exc))

    path = PLUGIN_ROOT / slug / "__init__.py"
    if not path.is_file():
        return PluginManifest(
            slug,
            {},
            installed=False,
            mountable=False,
            problem=f"No plugin named {slug!r} is installed on this instance",
        )

    try:
        source = path.read_bytes()
    except OSError as exc:
        return PluginManifest(
            slug, {}, installed=True, mountable=False, problem=f"Its __init__.py could not be read: {exc}"
        )

    if len(source) > MANIFEST_BYTE_LIMIT:
        return PluginManifest(
            slug,
            {},
            installed=True,
            mountable=False,
            problem=(
                f"Its __init__.py is larger than the {MANIFEST_BYTE_LIMIT} byte limit "
                "this backend will parse"
            ),
        )

    try:
        tree = ast.parse(source.decode("utf-8", errors="replace"), filename=str(path))
    except SyntaxError as exc:
        return PluginManifest(
            slug, {}, installed=True, mountable=False, problem=f"Its __init__.py does not parse: {exc}"
        )

    info = _declared_manifest(tree)
    declares_factory = _declares_factory(tree)

    problem: str | None = None
    if not declares_factory:
        problem = (
            f"It declares no {PLUGIN_FACTORY}() function, so this backend cannot construct it. "
            "Plugins written for the legacy Flask backend have this shape and must be "
            "adapted before they can be activated here."
        )
    elif not info:
        problem = f"It declares no {PLUGIN_MANIFEST} dictionary"

    return PluginManifest(
        slug,
        info,
        installed=True,
        mountable=declares_factory and bool(info),
        problem=problem,
    )


def _declares_factory(tree: ast.Module) -> bool:
    """Whether ``build`` is bound at the top level of the module.

    Accepts every ordinary way of providing it - defining it, assigning it, or
    re-exporting it from a submodule - because a plugin is entitled to organise
    itself, and this check exists to tell two backends apart rather than to
    dictate a file layout.
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == PLUGIN_FACTORY:
            return True
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == PLUGIN_FACTORY for target in node.targets
        ):
            return True
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == PLUGIN_FACTORY:
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == PLUGIN_FACTORY:
                    return True
    return False


def _declared_manifest(tree: ast.Module) -> dict:
    """The ``plugin_info`` dict, evaluated as data.

    Falls back to a key-by-key read when the dict as a whole will not evaluate:
    one entry built from a call or a variable is common enough, and losing the
    plugin's name and description over it would leave a blank row on the
    plugins screen. Nothing here is executed - a value that is not a literal is
    dropped, not resolved.
    """
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue

        if not any(isinstance(t, ast.Name) and t.id == PLUGIN_MANIFEST for t in targets):
            continue
        if node.value is None:
            continue

        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            value = _literal_items(node.value)

        return value if isinstance(value, dict) else {}
    return {}


#: Names a plugin may wrap a display string in. Unwrapped when reading the
#: manifest statically, because the value inside is what a reader needs and the
#: call cannot be evaluated without executing the module.
_TRANSLATION_CALLS = frozenset({"_", "gettext", "lazy_gettext"})


def _unwrap_translation(node: ast.expr) -> ast.expr:
    """``_("Atlas Wiki")`` -> ``"Atlas Wiki"``.

    Declaring a plugin's display strings translated is the documented pattern,
    and the template does it - so a manifest read that cannot see through the
    call reports the plugin with NO NAME. It is then listed as a blank row in
    the admin table: present, unnameable, and impossible to tell apart from
    another one in the same state.

    The untranslated text is the right answer here regardless of locale: this
    reader must not execute plugin code, and a name is better than nothing.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _TRANSLATION_CALLS
        and len(node.args) == 1
    ):
        return node.args[0]
    return node


def _literal_items(node: ast.expr) -> dict:
    """The entries of a dict literal whose key and value are both literals."""
    if not isinstance(node, ast.Dict):
        return {}

    items: dict = {}
    for key_node, value_node in zip(node.keys, node.values):
        if key_node is None:  # `**other` - nothing to read without executing it
            continue
        try:
            key = ast.literal_eval(key_node)
            items[key] = ast.literal_eval(_unwrap_translation(value_node))
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
    return items


# ---------------------------------------------------------------------------
# What is active
# ---------------------------------------------------------------------------


def get_active_plugin_slugs(mongo: Any | None = None) -> list[str]:
    """Read the active plugin list from the ``system`` collection.

    Returns validated slugs. An entry that fails validation is dropped with a
    warning rather than aborting the whole list, so one malformed row cannot
    take an instance down - but it is never imported.
    """
    if mongo is None:
        from archihub.infra.mongo import get_mongo

        mongo = get_mongo()

    record = mongo.get_record("system", {"name": "active_plugins"}, fields={"data": 1}) or {}
    slugs: list[str] = []
    for raw in record.get("data") or []:
        try:
            slugs.append(_validate_slug(raw))
        except PluginDiscoveryError as exc:
            logger.error("Skipping plugin entry: %s", exc)
    return slugs


def unported_plugins_allowed() -> bool:
    return os.environ.get("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def check_active_plugins(active_slugs: list[str]) -> None:
    """Refuse to start when an active plugin cannot be mounted here.

    Both the web process and the Celery worker enforce this: a worker that
    started while the web process refused would run tasks against an instance
    whose routes do not exist.

    The two reasons are separated in the message because the remedies are
    different - one is a missing directory, the other is a plugin that needs
    adapting - and an operator reading a startup failure has no other source of
    that distinction.
    """
    missing: list[str] = []
    incompatible: list[tuple[str, str]] = []

    for slug in active_slugs:
        manifest = read_manifest(slug)
        if manifest.mountable:
            continue
        if not manifest.installed:
            missing.append(slug)
        else:
            incompatible.append((slug, manifest.problem or "it cannot be built by this backend"))

    if not missing and not incompatible:
        return

    lines = [
        "Refusing to start: active plugins cannot be mounted by this backend.",
        "",
        "Starting anyway would either fail later with an opaque import error or -",
        "worse - appear to start while silently serving an instance whose features",
        "are missing.",
        "",
    ]
    if missing:
        lines += [
            f"  Active but not installed ({len(missing)}): {', '.join(sorted(missing))}",
            f"    -> no such package under {PLUGIN_PACKAGE}. Install the plugin, or",
            "       deactivate it on this instance.",
            "",
        ]
    for slug, problem in sorted(incompatible):
        lines += [
            f"  Installed but unusable: {slug}",
            f"    -> {problem}",
            "",
        ]
    lines += [
        "To deactivate a plugin, remove its slug from the `data` array of the",
        "`active_plugins` document in the `system` MongoDB collection.",
        "",
        "To bypass this check while developing against a disposable instance only,",
        "set ARCHIHUB_ALLOW_UNPORTED_PLUGINS=true. Never set it on a real deployment:",
        "the routes and scheduled tasks those plugins provide will simply not exist.",
    ]
    raise IncompatiblePluginError("\n".join(lines))


def assert_active_plugins_are_mountable(mongo: Any | None = None) -> list[str]:
    """Enforce the startup guard. Returns the active slugs when they all pass."""
    if unported_plugins_allowed():
        # Bypass mode must never block startup, including when there is no
        # database to ask - it is used in tests and in local development.
        try:
            slugs = get_active_plugin_slugs(mongo)
        except Exception as exc:
            logger.debug("Plugin check skipped (bypass set, settings unreadable): %s", exc)
            return []

        skipped = [slug for slug in slugs if not is_mountable(slug)]
        if skipped:
            logger.warning(
                "ARCHIHUB_ALLOW_UNPORTED_PLUGINS is set - starting WITHOUT these active "
                "plugins, whose routes and scheduled tasks will not exist: %s",
                ", ".join(sorted(skipped)),
            )
        return slugs

    try:
        slugs = get_active_plugin_slugs(mongo)
    except Exception as exc:
        # An unreachable database is a DIFFERENT failure from a plugin that
        # cannot be mounted, and conflating them sends an operator hunting the
        # wrong problem. Both are fatal - the application cannot serve a single
        # route without MongoDB, so coming up to emit 500s helps nobody - but
        # the message has to say which one happened.
        raise PluginDiscoveryError(
            "Refusing to start: could not read the active plugin list from MongoDB, "
            "so it is not possible to verify that every active plugin is supported "
            f"by this backend. Underlying error: {exc}"
        ) from exc

    check_active_plugins(slugs)
    return slugs


# ---------------------------------------------------------------------------
# Loading, for the plugins that will actually run
# ---------------------------------------------------------------------------


def import_plugin(slug: str) -> ModuleType:
    """Import a plugin package by slug.

    ``archihub.plugins`` is the ONLY location searched, and there is no fallback
    to any other plugin directory. ``app.plugins.<slug>`` is a subpackage of
    ``app``, whose ``__init__`` builds and boots an entire Flask application at
    module scope - a ``torch`` import and monkeypatch, Mongo reads, a
    SkillManager thread, plugin registration, a Babel instance. Reading one
    metadata dictionary from there would drag a second, fully-initialised web
    framework into the Celery worker or the ASGI process.
    """
    _validate_slug(slug)

    # A package sitting in this directory is not necessarily code this backend
    # can run, and importing one to find out is not free: a plugin package
    # written for another framework pulls that framework in at module scope,
    # standing a second application up inside this process - heavyweight
    # imports, database reads, a competing event registry - as a side effect of
    # reading a metadata dictionary. It gets far enough to do all of that
    # before failing on something else.
    #
    # The static manifest answers "can this backend build it" without executing
    # anything, so it is consulted first. Refusing here is what makes
    # `read_manifest`'s guarantee - that listing plugins never runs their code -
    # hold for every caller rather than only the listing route.
    manifest = read_manifest(slug)
    if not manifest.mountable:
        raise PluginDiscoveryError(
            f"Plugin {slug!r} cannot be loaded by this backend: "
            f"{manifest.problem or 'it is not supported'}"
        )

    try:
        return importlib.import_module(f"{PLUGIN_PACKAGE}.{slug}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{PLUGIN_PACKAGE}.{slug}":
            raise PluginDiscoveryError(
                f"Plugin {slug!r} is not installed in {PLUGIN_PACKAGE}"
            ) from exc
        # A ModuleNotFoundError from one of the plugin's OWN imports is a real
        # failure - a missing dependency - and must not be reported as "plugin
        # not found", which would send someone looking in the wrong place.
        raise PluginDiscoveryError(
            f"Plugin {slug!r} failed to import: missing dependency {exc.name!r}"
        ) from exc


def get_plugin_info(slug: str) -> dict:
    """A plugin's ``plugin_info`` as the imported module defines it, or ``{}``.

    Used where the plugin is being loaded anyway - the beat schedule, the
    mounted plugins' actions - so it reads the real object rather than the
    statically parsed one, and picks up anything computed at import time. Use
    :func:`read_manifest` where a plugin must not be executed.

    Never raises: the beat scheduler calls this for every active plugin on a
    timer, and one broken plugin must not stop the others from being scheduled.
    """
    try:
        module = import_plugin(slug)
    except Exception as exc:
        logger.warning("Could not load plugin %s for metadata: %s", slug, exc)
        return {}

    info = getattr(module, PLUGIN_MANIFEST, None)
    if not isinstance(info, dict):
        logger.warning("Plugin %s exposes no plugin_info dict", slug)
        return {}
    return info


def plugin_has_capability(slug: str, capability: str) -> bool:
    """Whether a plugin declares a capability (e.g. ``scheduler``)."""
    capabilities = get_plugin_info(slug).get("capabilities") or []
    return capability in capabilities
