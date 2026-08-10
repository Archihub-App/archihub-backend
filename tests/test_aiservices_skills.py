"""Skills: Markdown instruction files kept in step with a collection.

The first section is the one that matters. A skill path arrives in a URL and
becomes a file that is written, read and deleted, so containment is the security
boundary — and it is checked by resolving, not by inspecting the string, which
is what also closes the symlink case.
"""

from __future__ import annotations

import datetime

import pytest

from archihub.api.aiservices import skills


class FakeMongo:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.operators: list = []

    def get_record(self, collection, filters=None, fields=None):
        path = (filters or {}).get("path")
        row = self.rows.get(path)
        if row is None:
            return None
        if (filters or {}).get("active") and not row.get("active", True):
            return None
        return dict(row)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        rows = [dict(r) for r in self.rows.values() if r.get("active", True)]
        return sorted(rows, key=lambda r: r.get("path") or "")

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.operators.append((filters, operator))
        path = filters.get("path")
        values = operator.get("$set", {})
        if path in self.rows:
            self.rows[path].update(values)
        elif kwargs.get("upsert"):
            self.rows[path] = dict(values)


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    (tmp_path / "outside.md").write_text("# Not a skill\nsecrets")

    class Settings:
        llm_skills_path = str(root)

    monkeypatch.setattr(skills, "get_settings", lambda: Settings())
    monkeypatch.setattr("archihub.core.files.get_settings", lambda: Settings())
    return root


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(skills, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# The path boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "../../etc/passwd",
        "a/../../outside",
        "a/b/../../../outside",
        "a/./../../outside",
    ],
)
def test_a_path_that_leaves_the_skills_directory_is_refused(skills_root, path):
    """Checked by resolving, not by testing whether the text starts with '..'.

    The legacy check reasoned about the string; this reasons about where the
    path lands, which is the thing that matters and the thing that also covers
    symlinks.
    """
    with pytest.raises(skills.SkillError) as exc:
        skills.normalise(path)

    assert exc.value.status_code == 400


@pytest.mark.parametrize("path", ["", "   ", None, "/", 42, "//"])
def test_an_empty_or_unusable_path_is_refused(skills_root, path):
    with pytest.raises(skills.SkillError):
        skills.normalise(path)


@pytest.mark.parametrize("path", ["/etc/passwd", "/summarise"])
def test_an_absolute_path_is_refused_rather_than_reinterpreted(skills_root, path):
    """It is contained either way - the legacy code stripped the leading slash,
    so `/etc/passwd` became a real skill at `<skills>/etc/passwd.md`. Contained,
    but a request that plainly meant something else quietly succeeding at
    something surprising."""
    with pytest.raises(skills.SkillError):
        skills.normalise(path)


def test_a_literal_dotted_directory_name_stays_inside_and_is_allowed(skills_root):
    """`....` is a directory called four dots, not a traversal. Containment is
    decided by where it resolves, so it needs no special case."""
    assert skills.normalise("....//outside") == "..../outside.md"


@pytest.mark.parametrize(
    "given,expected",
    [
        ("summarise", "summarise.md"),
        ("summarise.md", "summarise.md"),
        ("folder/summarise", "folder/summarise.md"),
        ("folder\\summarise", "folder/summarise.md"),
        ("folder/summarise/", "folder/summarise.md"),
        ("folder//summarise", "folder/summarise.md"),
        ("./folder/summarise", "folder/summarise.md"),
    ],
)
def test_a_usable_path_normalises_to_one_stored_form(skills_root, given, expected):
    assert skills.normalise(given) == expected


def test_a_symlinked_skill_is_ignored_by_the_scan(skills_root, mongo, tmp_path):
    """A link inside the directory pointing out of it would otherwise have its
    target read and copied into the collection."""
    secret = tmp_path / "outside.md"
    (skills_root / "innocent.md").symlink_to(secret)
    (skills_root / "real.md").write_text("# Real\nbody")

    found = skills._scan()

    assert set(found) == {"real.md"}


def test_a_file_that_is_not_markdown_is_ignored(skills_root, mongo):
    (skills_root / "notes.txt").write_text("not a skill")
    (skills_root / "real.md").write_text("# Real")

    assert set(skills._scan()) == {"real.md"}


def test_hidden_directories_are_skipped(skills_root, mongo):
    hidden = skills_root / ".git"
    hidden.mkdir()
    (hidden / "config.md").write_text("# Not a skill")
    (skills_root / "real.md").write_text("# Real")

    assert set(skills._scan()) == {"real.md"}


def test_an_undecodable_file_is_skipped_rather_than_failing_the_scan(skills_root, mongo):
    (skills_root / "binary.md").write_bytes(b"\xff\xfe\x00\x01")
    (skills_root / "real.md").write_text("# Real")

    assert set(skills._scan()) == {"real.md"}


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


def test_the_title_is_the_first_markdown_heading():
    assert skills.title_of("a/b.md", "intro\n# The Real Title\nmore") == "The Real Title"


def test_a_skill_with_no_heading_is_titled_by_its_filename():
    assert skills.title_of("folder/summarise.md", "no heading here") == "summarise"


def test_the_command_is_the_path_without_its_suffix():
    assert skills.command_of("folder/summarise.md") == "folder/summarise"


def test_the_presented_shape_omits_content_unless_asked():
    record = {"path": "a.md", "name": "a", "content": "secret-ish"}

    assert "content" not in skills.present(record)
    assert skills.present(record, include_content=True)["content"] == "secret-ish"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_saving_a_skill_writes_the_file_and_records_it(skills_root, mongo):
    result = skills.save_skill("folder/summarise", "# Summarise\nDo it well.", "alice")

    assert (skills_root / "folder" / "summarise.md").read_text() == "# Summarise\nDo it well."
    assert result["path"] == "folder/summarise.md"
    assert result["title"] == "Summarise"
    assert result["command"] == "folder/summarise"
    assert result["folder"] == "folder"
    assert mongo.rows["folder/summarise.md"]["active"] is True


def test_saving_leaves_no_partial_file_behind(skills_root, mongo):
    """Written to a neighbour and moved into place, so a failure part way
    through cannot leave something the next sync treats as authoritative."""
    skills.save_skill("a", "# A")

    assert [p.name for p in skills_root.iterdir()] == ["a.md"]


@pytest.mark.parametrize("content", ["", "   ", None, 42])
def test_a_skill_with_no_content_is_refused(skills_root, mongo, content):
    with pytest.raises(skills.SkillError) as exc:
        skills.save_skill("a", content)

    assert exc.value.status_code == 400
    assert list(skills_root.iterdir()) == []


def test_an_oversized_skill_is_refused(skills_root, mongo):
    """A skill is an instruction sheet, not a corpus - and this is a request
    body that gets written to disk. The legacy write had no limit at all."""
    with pytest.raises(skills.SkillError) as exc:
        skills.save_skill("a", "x" * (skills.MAX_CONTENT_BYTES + 1))

    assert exc.value.status_code == 413
    assert list(skills_root.iterdir()) == []


def test_saving_over_an_existing_skill_keeps_its_creation_date(skills_root, mongo):
    skills.save_skill("a", "# One")
    created = mongo.rows["a.md"]["created_at"]

    skills.save_skill("a", "# Two")

    assert mongo.rows["a.md"]["created_at"] == created
    assert mongo.rows["a.md"]["title"] == "Two"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_getting_a_missing_skill_is_a_404(skills_root, mongo):
    with pytest.raises(skills.SkillError) as exc:
        skills.get_skill("nope")

    assert exc.value.status_code == 404


def test_a_retired_skill_is_not_returned(skills_root, mongo):
    skills.save_skill("a", "# A")
    skills.delete_skill("a")

    with pytest.raises(skills.SkillError):
        skills.get_skill("a")


def test_the_search_term_is_escaped_before_it_reaches_mongo(skills_root, mongo, monkeypatch):
    """A search box is not a place to accept a regular expression."""
    captured = {}

    def capture(collection, filters=None, **kwargs):
        captured.update(filters or {})
        return []

    monkeypatch.setattr(mongo, "get_all_records", capture)
    skills.list_skills("(a+)+$")

    pattern = captured["$or"][0]["path"]["$regex"]
    assert pattern == r"\(a\+\)\+\$"


def test_the_tree_view_groups_by_folder():
    items = [
        {"path": "top.md", "folder": ""},
        {"path": "a/one.md", "folder": "a"},
        {"path": "a/b/two.md", "folder": "a/b"},
    ]

    tree = skills.build_tree(items)

    assert [node.get("name") or node["path"] for node in tree] == ["top.md", "a"]
    folder = tree[1]
    assert folder["type"] == "folder"
    assert [child.get("name") or child["path"] for child in folder["children"]] == ["a/one.md", "b"]


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


def test_deleting_removes_the_file_and_retires_the_record(skills_root, mongo):
    skills.save_skill("folder/a", "# A")

    skills.delete_skill("folder/a", "alice")

    assert not (skills_root / "folder" / "a.md").exists()
    assert mongo.rows["folder/a.md"]["active"] is False


def test_deleting_prunes_the_folder_it_emptied(skills_root, mongo):
    skills.save_skill("folder/a", "# A")

    skills.delete_skill("folder/a")

    assert not (skills_root / "folder").exists()
    assert skills_root.exists()


def test_deleting_something_that_never_existed_is_a_404(skills_root, mongo):
    with pytest.raises(skills.SkillError) as exc:
        skills.delete_skill("nope")

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def test_a_file_with_no_record_is_recorded(skills_root, mongo):
    (skills_root / "new.md").write_text("# New")

    skills.sync()

    assert mongo.rows["new.md"]["title"] == "New"


def test_a_record_with_no_file_is_written_out(skills_root, mongo):
    """This is what makes a multi-node deployment work: a skill created on one
    node appears on the next node's disk."""
    mongo.rows["remote.md"] = {
        "path": "remote.md",
        "content": "# Remote",
        "content_hash": skills.digest("# Remote"),
        "active": True,
        "updated_at": datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
    }

    skills.sync()

    assert (skills_root / "remote.md").read_text() == "# Remote"


def test_the_newer_side_wins_when_both_have_changed(skills_root, mongo):
    import os
    import time

    (skills_root / "both.md").write_text("# From the file")
    mongo.rows["both.md"] = {
        "path": "both.md",
        "content": "# From the database",
        "content_hash": skills.digest("# From the database"),
        "active": True,
        "updated_at": datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc),
    }
    now = time.time()
    os.utime(skills_root / "both.md", (now, now))

    skills.sync()

    assert mongo.rows["both.md"]["title"] == "From the file"


def test_the_database_wins_when_it_is_the_newer_side(skills_root, mongo):
    import os

    (skills_root / "both.md").write_text("# From the file")
    old = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
    os.utime(skills_root / "both.md", (old, old))

    mongo.rows["both.md"] = {
        "path": "both.md",
        "content": "# From the database",
        "content_hash": skills.digest("# From the database"),
        "active": True,
        "updated_at": datetime.datetime.now(datetime.timezone.utc),
    }

    skills.sync()

    assert (skills_root / "both.md").read_text() == "# From the database"


def test_one_unreadable_skill_does_not_abort_the_whole_sync(skills_root, mongo, monkeypatch):
    """The legacy version let the exception escape, leaving every other skill
    unsynchronised because one file was bad."""
    (skills_root / "good.md").write_text("# Good")
    mongo.rows["broken.md"] = {"path": "broken.md", "content": "x", "active": True}

    original = skills._write_record

    def explode(record):
        if record.get("path") == "broken.md":
            raise OSError("disk on fire")
        return original(record)

    monkeypatch.setattr(skills, "_write_record", explode)

    synced = skills.sync()

    assert [s["path"] for s in synced] == ["good.md"]
    assert mongo.rows["good.md"]["title"] == "Good"


def test_a_stored_skill_with_an_unusable_path_is_ignored(skills_root, mongo):
    mongo.rows["../escape.md"] = {"path": "../escape.md", "content": "x", "active": True}
    (skills_root / "good.md").write_text("# Good")

    synced = skills.sync()

    assert [s["path"] for s in synced] == ["good.md"]


def test_syncing_an_unchanged_pair_refreshes_the_record(skills_root, mongo):
    content = "# Same"
    (skills_root / "same.md").write_text(content)
    mongo.rows["same.md"] = {
        "path": "same.md",
        "content": content,
        "content_hash": skills.digest(content),
        "active": True,
        "updated_at": datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc),
    }

    synced = skills.sync()

    assert [s["path"] for s in synced] == ["same.md"]
    assert (skills_root / "same.md").read_text() == content
