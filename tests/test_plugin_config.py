"""Per-plugin configuration.

A plugin owns its configuration the same way it owns its Python dependencies and
its system packages: declared, documented and supplied beside the plugin. These
tests hold the two halves of that — that a plugin can read its own settings, and
that the backend does not acquire fields for variables only a plugin uses.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from archihub.plugins.framework import config

PLUGIN_ROOT = pathlib.Path("archihub/plugins")


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """Point the loader at a throwaway plugin directory."""
    directory = tmp_path / "somePlugin"
    directory.mkdir()
    monkeypatch.setattr(config, "_plugin_directory", lambda slug: directory)
    return directory


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_a_plugin_reads_its_own_env_file(plugin_dir, monkeypatch):
    monkeypatch.delenv("SOME_TOKEN", raising=False)
    (plugin_dir / ".env").write_text("SOME_TOKEN=from-the-file\n")

    assert config.get("somePlugin", "SOME_TOKEN") == "from-the-file"


def test_the_environment_wins_over_the_file(plugin_dir, monkeypatch):
    """A container injects real variables and has no file at all.

    So the file is the fallback, and a deployment can override one value without
    editing something inside the plugin directory.
    """
    (plugin_dir / ".env").write_text("SOME_TOKEN=from-the-file\n")
    monkeypatch.setenv("SOME_TOKEN", "from-the-environment")

    assert config.get("somePlugin", "SOME_TOKEN") == "from-the-environment"


def test_a_missing_setting_falls_back_to_the_default(plugin_dir, monkeypatch):
    monkeypatch.delenv("ABSENT", raising=False)
    assert config.get("somePlugin", "ABSENT", "a-default") == "a-default"


def test_a_required_setting_that_is_absent_says_where_to_put_it(plugin_dir, monkeypatch):
    """An absent credential returned as "" is handed to a remote service and
    comes back as an authentication failure naming nothing actionable."""
    monkeypatch.delenv("NEEDED", raising=False)

    with pytest.raises(config.MissingPluginSetting) as exc:
        config.get("somePlugin", "NEEDED", required=True)

    assert "NEEDED" in str(exc.value)
    assert "somePlugin" in str(exc.value)


def test_a_blank_value_counts_as_absent(plugin_dir, monkeypatch):
    """Whitespace is what a half-filled `.env.example` leaves behind."""
    (plugin_dir / ".env").write_text("SOME_TOKEN=   \n")
    monkeypatch.delenv("SOME_TOKEN", raising=False)

    assert config.get("somePlugin", "SOME_TOKEN", "fallback") == "fallback"
    with pytest.raises(config.MissingPluginSetting):
        config.get("somePlugin", "SOME_TOKEN", required=True)


def test_an_unreadable_env_file_does_not_stop_the_plugin(plugin_dir, monkeypatch):
    """Its other settings may come from the environment, and failing at import
    takes the whole instance down for one malformed line."""
    (plugin_dir / ".env").write_bytes(b"\xff\xfe not utf-8 at all")
    monkeypatch.setenv("SOME_TOKEN", "from-the-environment")

    assert config.get("somePlugin", "SOME_TOKEN") == "from-the-environment"


def test_a_plugin_with_no_env_file_is_not_an_error(plugin_dir, monkeypatch):
    monkeypatch.delenv("SOME_TOKEN", raising=False)
    assert config.read_env_file("somePlugin") == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("on", True), ("false", False), ("0", False), ("", False)],
)
def test_a_flag_accepts_only_the_shell_spellings(plugin_dir, monkeypatch, raw, expected):
    """A bare truthiness check reads "0" and "false" as enabled."""
    monkeypatch.setenv("SOME_FLAG", raw) if raw else monkeypatch.delenv("SOME_FLAG", raising=False)
    assert config.get_bool("somePlugin", "SOME_FLAG") is expected


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def test_no_plugin_calls_load_dotenv():
    """It mutates the process-wide environment.

    What one plugin can then see depends on which OTHER plugins were imported
    first - an ordering dependency between components that are meant to be
    independent, and one that behaves differently in a container (where the
    variables are already set) from a developer's machine.
    """
    offenders = []
    # The framework is included deliberately: `plugins.framework.config` is the
    # replacement for this call, so it is the one module that must never make it.
    for path in PLUGIN_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(errors="replace"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "load_dotenv":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], "read settings through plugins.framework.config instead: " + ", ".join(offenders)


def test_the_backend_declares_no_setting_that_ONLY_a_plugin_reads():
    """A variable only a plugin uses is that plugin's to declare and supply.

    Putting it in the backend's settings makes every deployment carry a field
    for a component it may not have installed, and puts the plugin's credential
    in the object the whole application reads. It also splits ownership: the
    plugin ships its own dependencies and system packages, so its configuration
    belonging somewhere else is the odd one out.

    Only settings read by plugins AND NOT by the backend are flagged. A setting
    nothing reads is dead configuration - a different problem, and not one this
    boundary is about.
    """
    import ast as ast_
    import re

    declaration = pathlib.Path("archihub/core/settings.py")
    tree = ast_.parse(declaration.read_text())

    # The field name and the environment variable are frequently different
    # (MONGO_INITDB_ROOT_PASSWORD is `mongo_password`), so both are taken from
    # the declaration rather than derived from each other.
    fields: dict[str, str] = {}
    for node in ast_.walk(tree):
        if not isinstance(node, ast_.AnnAssign) or not isinstance(node.target, ast_.Name):
            continue
        for kw in getattr(node.value, "keywords", []):
            if kw.arg == "validation_alias" and isinstance(kw.value, ast_.Constant):
                fields[kw.value.value] = node.target.id

    def is_plugin(path: pathlib.Path) -> bool:
        return path.parts[:2] == ("archihub", "plugins") and "framework" not in path.parts

    def names_used(paths, skip_declarations: bool = False) -> set[str]:
        """Attribute names and string literals actually present in the code.

        READ FROM THE AST, NOT THE TEXT. A comment or a docstring that happens to
        mention a variable is not a use of it - matching raw text made this check
        pass because one core module lists `HF_TOKEN` in a comment about
        third-party SDKs.
        """
        found: set[str] = set()
        for path in paths:
            tree = ast_.parse(path.read_text(errors="replace"))
            docstrings = {
                node.value
                for node in ast_.walk(tree)
                if isinstance(node, ast_.Expr) and isinstance(node.value, ast_.Constant)
            }
            for node in ast_.walk(tree):
                if isinstance(node, ast_.Attribute):
                    found.add(node.attr)
                elif isinstance(node, ast_.Constant) and isinstance(node.value, str):
                    if node not in docstrings:
                        found.add(node.value)
                elif skip_declarations and isinstance(node, ast_.keyword):
                    # The declaration itself is not a use: several fields are
                    # consumed only by the derived properties beside them, and
                    # those reads are what must count.
                    if node.arg == "validation_alias":
                        found.discard(getattr(node.value, "value", None))
        return found

    plugin_names = names_used([p for p in PLUGIN_ROOT.rglob("*.py") if is_plugin(p)])

    core_paths = [p for p in pathlib.Path("archihub").rglob("*.py") if not is_plugin(p)]
    core_names = names_used(core_paths)
    for alias, _field in fields.items():
        # Remove the declarations themselves, keeping any real read of the field.
        if alias in core_names and not re.search(
            rf"\.{fields[alias]}\b",
            "\n".join(
                "\n".join(
                    line
                    for line in p.read_text(errors="replace").splitlines()
                    if p != declaration or "validation_alias=" not in line
                )
                for p in core_paths
            ),
        ):
            core_names.discard(alias)

    # Some settings are consumed by the entrypoint rather than by Python.
    shell = "\n".join(pathlib.Path(s).read_text() for s in ("start.sh", "start_celery.sh"))

    def used_by_core(alias: str, field: str) -> bool:
        return field in core_names or alias in core_names or re.search(rf"\b{alias}\b", shell)

    def used_by_plugin(alias: str, field: str) -> bool:
        return field in plugin_names or alias in plugin_names

    plugin_only = sorted(
        alias
        for alias, field in fields.items()
        if used_by_plugin(alias, field) and not used_by_core(alias, field)
    )

    assert plugin_only == [], (
        "these are read only by plugins and belong in the plugin's own .env, "
        "not in the backend's settings: " + ", ".join(plugin_only)
    )


def test_a_plugin_env_file_is_never_committed_or_shipped():
    """It holds credentials, exactly like the backend's own `.env`.

    The five bundled plugins are tracked, so without an explicit rule theirs
    would be committed to a public repository.
    """
    for name in (".gitignore", ".dockerignore"):
        text = pathlib.Path(name).read_text()
        assert "archihub/plugins/*/.env" in text, f"{name} must exclude plugin env files"


def test_every_declared_setting_is_actually_read():
    """A field nothing reads is a promise the application does not keep.

    An operator setting it sees no effect and has nothing to debug; a reader
    seeing it declared assumes it does something. Three had accumulated:
    `SECRET_KEY` signed the previous framework's sessions, `ENVIRONMENT_NAME` is
    substituted by the deployment into the database name rather than read here,
    and `jwt_access_token_expires` was superseded by a constant in the token
    module.

    A setting consumed by the entrypoint scripts counts as read - that is where
    the worker count and the port are used.
    """
    import ast as ast_
    import re

    declaration = pathlib.Path("archihub/core/settings.py")
    tree = ast_.parse(declaration.read_text())

    declared: dict[str, str | None] = {}
    for node in ast_.walk(tree):
        if not isinstance(node, ast_.AnnAssign) or not isinstance(node.target, ast_.Name):
            continue
        alias = None
        for kw in getattr(node.value, "keywords", []):
            if kw.arg == "validation_alias" and isinstance(kw.value, ast_.Constant):
                alias = kw.value.value
        declared[node.target.id] = alias

    # The declaration lines themselves are not a use, but the derived properties
    # beside them are - several fields are read only there.
    body = "\n".join(
        line
        for line in declaration.read_text().splitlines()
        if "validation_alias=" not in line
    )
    code = body + "\n" + "\n".join(
        p.read_text(errors="replace")
        for p in pathlib.Path("archihub").rglob("*.py")
        if p != declaration
    )
    shell = "\n".join(
        pathlib.Path(s).read_text() for s in ("start.sh", "start_celery.sh", "Dockerfile")
    )

    unread = sorted(
        field
        for field, alias in declared.items()
        if not re.search(rf"\.{field}\b", code)
        and not (alias and re.search(rf"\b{alias}\b", shell))
    )

    assert unread == [], (
        "declared but never read - delete them, or read them where they were "
        "meant to apply: " + ", ".join(unread)
    )
