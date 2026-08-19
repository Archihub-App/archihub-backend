"""Skills: reusable instruction files an operator writes and the agent applies.

A skill is a Markdown file on disk **and** a row in Mongo, kept in step. The
filesystem is the editing surface — an operator can drop a file in with an
editor or a git checkout — and the database is what the chat path reads, because
resolving a skill per message by walking a directory would not survive a
multi-node deployment.

Two-way sync is the consequence, and it is the interesting part: on start-up
each side is compared and the newer wins, per file. Operators edit skills from
both ends - through the interface and by dropping a Markdown file on the box -
so neither side can be treated as authoritative.

**THE PATH IS THE SECURITY BOUNDARY.** A skill path arrives in a URL and becomes
a file that gets written, read and deleted. A string-based check on it - say::

    normalized = os.path.normpath(normalized).replace('\\\\', '/')
    if normalized.startswith('..'):
        raise ValueError(...)

which reasons about the *text* of a path rather than where it lands, and says
nothing about symlinks — a link inside the skills directory pointing at
``/etc`` was followed by the filesystem walk and its contents synced into the
database. Every path here goes through ``core.files.resolve_within``, which
resolves and then checks containment, so both cases fail closed. It is the same
helper the records viewers use, for the same reason.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import re
from pathlib import Path

from archihub.core import files as filestore
from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "llm_skills"

#: Skills are Markdown. Anything else in the directory is somebody's notes.
SKILL_SUFFIX = ".md"

#: Ceiling on a skill's content. A skill is an instruction sheet, not a corpus,
#: and this is a request body that gets written to disk — the legacy write had
#: no limit at all.
MAX_CONTENT_BYTES = 1024 * 1024

#: Directories skipped when walking. Dot-directories are editor and VCS state.
def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class SkillError(Exception):
    """The request does not describe a skill that can be acted on."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def root() -> Path:
    """Where skills live. Created on demand."""
    configured = get_settings().llm_skills_path
    path = Path(configured).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalise(skill_path: str | None) -> str:
    """The stored, relative form of a skill path.

    Rejects anything that would land outside the skills directory — checked by
    resolving, not by inspecting the string.
    """
    if not isinstance(skill_path, str) or not skill_path.strip():
        raise SkillError(_("Skill path is required"), 400)

    candidate = skill_path.replace("\\", "/").strip()

    # An absolute path is refused rather than quietly reinterpreted as relative.
    # The legacy code stripped the leading slash, so `/etc/passwd` became a real
    # skill at `<skills>/etc/passwd.md` - contained, and therefore not a security
    # problem, but a request that plainly meant something else succeeding at
    # something surprising.
    if candidate.startswith("/"):
        raise SkillError(_("Skill path must be relative to the skills directory"), 400)

    candidate = candidate.strip("/")
    if not candidate:
        raise SkillError(_("Skill path is required"), 400)
    if not candidate.lower().endswith(SKILL_SUFFIX):
        candidate = f"{candidate}{SKILL_SUFFIX}"

    base = root()
    try:
        resolved = filestore.resolve_within(base, candidate)
    except filestore.UnsupportedFile:
        raise SkillError(_("Skill path must stay inside the skills directory"), 400) from None

    relative = resolved.relative_to(base).as_posix()
    if not relative or relative == SKILL_SUFFIX:
        raise SkillError(_("Skill path is required"), 400)
    return relative


def absolute(relative_path: str) -> Path:
    return root() / relative_path


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

_TITLE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$")


def title_of(relative_path: str, content: str) -> str:
    """A skill's first Markdown heading, or its filename."""
    for line in content.splitlines():
        match = _TITLE.match(line)
        if match:
            return match.group(1).strip()
    return Path(relative_path).stem


def command_of(relative_path: str) -> str:
    """What an author types to invoke it: the path without its suffix."""
    return relative_path[: -len(SKILL_SUFFIX)] if relative_path.lower().endswith(SKILL_SUFFIX) else relative_path


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def present(record: dict, *, include_content: bool = False) -> dict:
    """A skill as the API returns it."""
    path = record.get("path") or ""
    shown = {
        "id": path,
        "path": path,
        "name": record.get("name"),
        "title": record.get("title") or record.get("name"),
        "command": record.get("command") or command_of(path),
        "folder": record.get("folder") or "",
        "content_hash": record.get("content_hash"),
        "created_at": _iso(record.get("created_at")),
        "updated_at": _iso(record.get("updated_at")),
    }
    if include_content:
        shown["content"] = record.get("content", "")
    return shown


def _iso(value):
    return value.isoformat() if isinstance(value, datetime.datetime) else value


def _fields(include_content: bool) -> dict:
    fields = {
        "path": 1, "name": 1, "title": 1, "command": 1, "folder": 1,
        "content_hash": 1, "updated_at": 1, "created_at": 1, "active": 1,
    }
    if include_content:
        fields["content"] = 1
    return fields


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def list_skills(query: str | None = None, *, include_content: bool = False, tree: bool = False):
    """Every active skill, optionally filtered and optionally as a folder tree."""
    filters: dict = {"active": True}

    term = (query or "").strip()
    if term:
        # Escaped: a skill search box is not a place to accept a regular
        # expression, and `(a+)+$` in one is a denial of service.
        pattern = {"$regex": re.escape(term), "$options": "i"}
        filters["$or"] = [{"path": pattern}, {"name": pattern}, {"title": pattern}, {"command": pattern}]

    rows = _mongo().get_all_records(
        COLLECTION, filters, sort=[("path", 1)], fields=_fields(include_content)
    )
    items = [present(row, include_content=include_content) for row in rows]
    return build_tree(items) if tree else items


def build_tree(items: list[dict]) -> list[dict]:
    """Group a flat skill list into the folder structure it came from."""
    folders: dict[str, dict] = {}
    roots: list[dict] = []

    for item in items:
        parts = [part for part in (item.get("folder") or "").split("/") if part]
        level = roots
        walked: list[str] = []

        for part in parts:
            walked.append(part)
            key = "/".join(walked)
            node = folders.get(key)
            if node is None:
                node = {"type": "folder", "name": part, "path": key, "children": []}
                folders[key] = node
                level.append(node)
            level = node["children"]

        level.append({"type": "skill", **item})

    return roots


def get_skill(skill_path: str, *, include_content: bool = True) -> dict:
    relative = normalise(skill_path)
    record = _mongo().get_record(
        COLLECTION, {"path": relative, "active": True}, fields=_fields(include_content)
    )
    if not record:
        raise SkillError(_("Skill not found"), 404)
    return present(record, include_content=include_content)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def save_skill(skill_path: str, content: str, user: str | None = None) -> dict:
    """Write a skill to disk and record it.

    The file is the source of truth for the sync that follows, so it is written
    first and the database row is derived from what actually landed.
    """
    relative = normalise(skill_path)

    if not isinstance(content, str) or not content.strip():
        raise SkillError(_("Skill content is required"), 400)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CONTENT_BYTES:
        raise SkillError(_("The skill is too large"), 413)

    target = absolute(relative)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Written to a temporary neighbour and moved into place, so a failure part
    # way through cannot leave a half-written skill that the next sync would
    # then copy into the database as authoritative.
    staging = target.with_name(target.name + ".partial")
    try:
        staging.write_text(content, encoding="utf-8")
        staging.replace(target)
    except OSError:
        filestore.remove_quietly(staging)
        logger.exception("Could not write the skill %s", relative)
        raise SkillError(_("The skill could not be saved"), 500) from None

    return _record_file(target, relative, user)


def delete_skill(skill_path: str, user: str | None = None) -> None:
    """Remove a skill's file and retire its record."""
    relative = normalise(skill_path)
    record = _mongo().get_record(COLLECTION, {"path": relative, "active": True}, fields={"path": 1})

    target = absolute(relative)
    removed = False
    if target.is_file():
        try:
            target.unlink()
            removed = True
        except OSError:
            logger.exception("Could not remove the skill file %s", relative)
            raise SkillError(_("The skill could not be deleted"), 500) from None

    if not record and not removed:
        raise SkillError(_("Skill not found"), 404)

    _mongo().update_record_operator(
        COLLECTION,
        {"path": relative},
        {"$set": {"active": False, "updated_at": _now(), "updatedBy": user or "system"}},
    )
    prune_empty_directories()


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def sync() -> list[dict]:
    """Reconcile the skills directory with the collection, newer side winning.

    Per file: present on one side only, it is copied to the other; present on
    both with the same content, the record is refreshed; otherwise the more
    recently modified side wins.
    """
    on_disk = _scan()
    in_database = _stored()

    synced = []
    for relative in sorted(set(on_disk) | set(in_database)):
        entry = on_disk.get(relative)
        record = in_database.get(relative)

        try:
            if entry and not record:
                synced.append(_record_file(entry["path"], relative))
            elif record and not entry:
                synced.append(_write_record(record))
            elif entry and record:
                if entry["hash"] == record.get("content_hash"):
                    synced.append(_record_file(entry["path"], relative))
                elif entry["modified"] >= _as_utc(record.get("updated_at")):
                    synced.append(_record_file(entry["path"], relative))
                else:
                    synced.append(_write_record(record))
        except (OSError, SkillError):
            # One unreadable skill does not abort the sync of every other. The
            # legacy version let the exception escape, so a single bad file left
            # the whole collection unsynchronised.
            logger.exception("Could not synchronise the skill %s", relative)

    prune_empty_directories()
    return synced


def _scan() -> dict[str, dict]:
    """Every Markdown file under the skills root, with its hash and mtime.

    Symlinks are refused rather than followed: one pointing outside the skills
    directory would otherwise have its target read and copied into the database.
    """
    base = root()
    found: dict[str, dict] = {}

    for directory, subdirectories, filenames in os.walk(base):
        subdirectories[:] = [name for name in subdirectories if not _is_hidden(name)]

        for filename in sorted(filenames):
            if not filename.lower().endswith(SKILL_SUFFIX) or _is_hidden(filename):
                continue

            path = Path(directory) / filename
            try:
                resolved = filestore.resolve_within(base, path.relative_to(base).as_posix())
            except (filestore.UnsupportedFile, ValueError):
                logger.warning("Ignoring a skill path that leaves the skills directory: %s", path)
                continue
            if path.is_symlink() or resolved != path.resolve():
                logger.warning("Ignoring the symlinked skill %s", path)
                continue

            try:
                content = path.read_text(encoding="utf-8")
                stat = path.stat()
            except (OSError, UnicodeDecodeError):
                logger.warning("Ignoring an unreadable skill file: %s", path)
                continue

            relative = path.relative_to(base).as_posix()
            found[relative] = {
                "path": path,
                "hash": digest(content),
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc),
            }

    return found


def _stored() -> dict[str, dict]:
    rows = _mongo().get_all_records(
        COLLECTION, {"active": True}, sort=[("path", 1)], fields=_fields(include_content=True)
    )
    stored: dict[str, dict] = {}
    for row in rows:
        try:
            stored[normalise(row.get("path"))] = row
        except SkillError:
            logger.warning("Ignoring a stored skill with an unusable path: %r", row.get("path"))
    return stored


def _record_file(path: Path, relative: str, user: str | None = None) -> dict:
    """Copy a file's contents into the collection."""
    content = path.read_text(encoding="utf-8")
    existing = _mongo().get_record(COLLECTION, {"path": relative}, fields={"created_at": 1})
    now = _now()

    payload = {
        "path": relative,
        "name": Path(relative).stem,
        "title": title_of(relative, content),
        "command": command_of(relative),
        "folder": str(Path(relative).parent).replace("\\", "/").strip(".").strip("/"),
        "content": content,
        "content_hash": digest(content),
        "active": True,
        "updated_at": now,
        "created_at": (existing or {}).get("created_at") or now,
    }
    if user:
        payload["updatedBy"] = user

    _mongo().update_record_operator(
        COLLECTION, {"path": relative}, {"$set": payload}, upsert=True
    )
    return present(payload, include_content=True)


def _write_record(record: dict) -> dict:
    """Copy a stored skill back out to disk."""
    relative = normalise(record.get("path"))
    target = absolute(relative)
    target.parent.mkdir(parents=True, exist_ok=True)

    content = record.get("content") or ""
    target.write_text(content, encoding="utf-8")

    # The file's mtime is set to the record's, so the next sync compares like
    # with like instead of always seeing the file as newer.
    stamp = _as_utc(record.get("updated_at")).timestamp()
    os.utime(target, (stamp, stamp))

    return _record_file(target, relative)


def _as_utc(value) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value.astimezone(datetime.timezone.utc)
    return datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)


def prune_empty_directories() -> None:
    """Remove folders a deletion emptied, leaving the root alone."""
    base = root()
    for directory, subdirectories, filenames in os.walk(base, topdown=False):
        path = Path(directory)
        if path == base or subdirectories or filenames:
            continue
        try:
            path.rmdir()
        except OSError:
            continue
