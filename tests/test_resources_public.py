"""The public resource mirror, and the shared file/presentation machinery.

The bulk download is where the weight is: BACKEND_FINDINGS S23 (the archive
path was built from the request, a file write to wherever the caller pointed it)
and S24 (files the caller could not see went into the archive anyway).
"""

from __future__ import annotations

import zipfile

import pytest
from bson.objectid import ObjectId

from archihub.api.resources import access, files, presentation, public

RESOURCE_ID = "6a70b833497d4440325c94b1"
OPEN_FILE = "6a70b833497d4440325c94c1"
RESERVED_FILE = "6a70b833497d4440325c94c2"


class FakeMongo:
    def __init__(self):
        self.resources: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.types: dict[str, dict] = {}
        self.settings: dict[str, dict] = {}
        self.distinct_values: list = []

    def get_record(self, collection, filters=None, fields=None):
        key = str((filters or {}).get("_id"))
        if collection == "post_types":
            slug = (filters or {}).get("slug")
            return self.types.get(slug) if slug else None
        if collection == "system":
            return self.settings.get((filters or {}).get("name"))
        if collection == "records":
            return self.records.get(key)
        return self.resources.get(key)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "post_types":
            wanted = set((filters or {}).get("slug", {}).get("$in", []))
            return [t for s, t in self.types.items() if s in wanted]
        source = self.records if collection == "records" else self.resources
        wanted = {str(o) for o in (filters or {}).get("_id", {}).get("$in", [])}
        rows = [r for k, r in source.items() if k in wanted]
        if collection == "records" and "$or" in (filters or {}):
            rows = [
                r for r in rows
                if ((r.get("processing") or {}).get("fileProcessing") or {}).get("type") != "image"
            ]
        return rows

    def count(self, collection, filters=None):
        wanted = {str(o) for o in (filters or {}).get("_id", {}).get("$in", [])}
        kind = (filters or {}).get("processing.fileProcessing.type")
        rows = [r for k, r in self.records.items() if k in wanted]
        if kind:
            rows = [
                r for r in rows
                if ((r.get("processing") or {}).get("fileProcessing") or {}).get("type") == kind
            ]
        return len(rows)

    def distinct(self, collection, field, filters=None):
        return list(self.distinct_values)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    for module in (files, presentation, public, access):
        monkeypatch.setattr(module, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.services._mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.hierarchy._mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.records.access._mongo", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)
    monkeypatch.setattr("archihub.api.resources.access.user_access_rights", lambda u: [])
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.type_roles",
        lambda slug: {"viewRoles": [], "editRoles": []},
    )


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    web = tmp_path / "web"
    originals = tmp_path / "original"
    web.mkdir()
    originals.mkdir()
    (tmp_path / "outside.txt").write_text("not yours")

    class Settings:
        web_files_path = str(web)
        original_files_path = str(originals)
        temporal_files_path = str(tmp_path / "tmp")

    monkeypatch.setattr("archihub.core.files.get_settings", lambda: Settings())
    monkeypatch.setattr("archihub.api.records.media.get_settings", lambda: Settings())
    monkeypatch.setattr("archihub.api.records.media.downloads_enabled", lambda: True)
    return tmp_path


def resource(**overrides):
    document = {
        "_id": ObjectId(RESOURCE_ID),
        "status": "published",
        "accessRights": None,
        "parents": [],
        "post_type": "carpeta",
        "metadata": {"firstLevel": {"title": "A folder"}},
        "filesObj": [{"id": OPEN_FILE, "tag": "doc", "order": 0}],
    }
    document.update(overrides)
    return document


def record(record_id, name, access_rights=None, kind="document"):
    return {
        "_id": ObjectId(record_id),
        "name": name,
        "displayName": name,
        "hash": "abc",
        "accessRights": access_rights,
        "filepath": f"2024/{name}",
        "processing": {"fileProcessing": {"type": kind, "path": f"2024/{name}"}},
    }


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_files_without_an_explicit_order_take_the_lowest_free_position():
    entries = files.ordered_file_entries(
        {"filesObj": [{"id": "a"}, {"id": "b", "order": 0}, {"id": "c"}]}
    )

    assert [e["id"] for e in entries] == ["b", "a", "c"]
    assert [e["order"] for e in entries] == [0, 1, 2]


def test_a_resource_with_no_files_has_no_entries():
    assert files.ordered_file_entries({}) == []


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_the_listing_returns_files_in_display_order(mongo):
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "one.pdf")
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "two.pdf")
    subject = resource(
        filesObj=[{"id": RESERVED_FILE, "order": 0}, {"id": OPEN_FILE, "order": 1}]
    )

    payload, status = files.list_files(subject, "alice", 0, False)

    assert status == 200
    assert [row["displayName"] for row in payload["data"]] == ["two.pdf", "one.pdf"]
    assert payload["total"] == 2


def test_a_restricted_file_is_listed_without_its_id(mongo):
    """The interface shows that something is there without it being fetchable."""
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "secret.pdf", access_rights="reserved")
    subject = resource(filesObj=[{"id": RESERVED_FILE, "order": 0}])

    payload, _status = files.list_files(subject, "alice", 0, False)

    assert payload["data"][0]["id"] is None
    assert payload["data"][0]["hash"] == ""
    assert "secret.pdf" not in payload["data"][0]["displayName"]


def test_a_public_listing_never_reveals_a_restricted_file(mongo):
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "secret.pdf", access_rights="reserved")
    subject = resource(filesObj=[{"id": RESERVED_FILE, "order": 0}])

    payload, _status = files.list_files(subject, None, 0, False, public=True)

    assert payload["data"][0]["id"] is None


def test_a_held_right_reveals_the_file(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.resources.access.user_access_rights", lambda u: ["reserved"]
    )
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "secret.pdf", access_rights="reserved")
    subject = resource(filesObj=[{"id": RESERVED_FILE, "order": 0}])

    payload, _status = files.list_files(subject, "alice", 0, False)

    assert payload["data"][0]["displayName"] == "secret.pdf"


def test_the_listing_never_carries_a_filepath(mongo):
    """Not even briefly - it is not fetched at all."""
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "one.pdf")

    payload, _status = files.list_files(resource(), "alice", 0, False)

    assert "filepath" not in payload["data"][0]


def test_counting_images_of_a_resource_with_none_is_a_404(mongo):
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "one.pdf")

    payload, status = files.count_images(resource())

    assert status == 404


def test_counting_images_reports_how_many(mongo):
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "one.jpg", kind="image")

    payload, status = files.count_images(resource())

    assert (payload, status) == ({"pages": 1}, 200)


# ---------------------------------------------------------------------------
# Bulk download
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", ["../../../../../../tmp/pwned", "../../evil", "/etc/passwd", "", "zip"]
)
def test_a_download_kind_outside_the_allowlist_is_refused(mongo, media_root, kind):
    """BACKEND_FINDINGS S23.

    The archive path was ``os.path.join(WEB_FILES_PATH, 'zipfiles', user + '-' +
    body['id'] + '-' + body['type'] + '.zip')``, so this was a file write to
    wherever the caller pointed it - verified resolving to ``/tmp/evil.zip`` -
    and the public route reached it unauthenticated.
    """
    with pytest.raises(files.DownloadRefused) as exc:
        files.bulk_download(resource(), kind, "alice")

    assert exc.value.status_code == 400


def test_the_archive_name_holds_no_client_string(mongo):
    name = files.archive_name(RESOURCE_ID, [{"_id": ObjectId(OPEN_FILE)}], "original")

    assert name.startswith(files.ZIP_PREFIX)
    assert name.endswith(".zip")
    assert "/" not in name and ".." not in name


def test_the_archive_name_changes_when_the_contents_change(mongo):
    """The original cached on a fixed name and served the stale archive forever."""
    first = files.archive_name(RESOURCE_ID, [{"_id": ObjectId(OPEN_FILE)}], "original")
    second = files.archive_name(
        RESOURCE_ID, [{"_id": ObjectId(OPEN_FILE)}, {"_id": ObjectId(RESERVED_FILE)}], "original"
    )

    assert first != second


def test_the_archive_name_distinguishes_the_two_kinds(mongo):
    records = [{"_id": ObjectId(OPEN_FILE)}]

    assert files.archive_name(RESOURCE_ID, records, "original") != files.archive_name(
        RESOURCE_ID, records, "small"
    )


def _place(media_root, name):
    path = media_root / "original" / "2024"
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(f"contents of {name}")


def test_an_archive_excludes_files_the_caller_may_not_see(mongo, media_root):
    """BACKEND_FINDINGS S24.

    The original kept restricted records in the list, only blanking the display
    name, and then wrote them into the archive by ``filepath`` - so a public
    bulk download shipped reserved files under a placeholder name.
    """
    second_open = "6a70b833497d4440325c94c3"
    for name in ("open.pdf", "also-open.pdf", "secret.pdf"):
        _place(media_root, name)
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "open.pdf")
    mongo.records[second_open] = record(second_open, "also-open.pdf")
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "secret.pdf", access_rights="reserved")
    subject = resource(
        filesObj=[
            {"id": OPEN_FILE, "order": 0},
            {"id": RESERVED_FILE, "order": 1},
            {"id": second_open, "order": 2},
        ]
    )

    response = files.bulk_download(subject, "original", None, public=True)

    with zipfile.ZipFile(response.path) as archive:
        assert sorted(archive.namelist()) == ["also-open.pdf", "open.pdf"]


def test_an_archive_entry_name_cannot_escape_the_extraction_directory(mongo, media_root):
    """A stored name with `..` in it would otherwise write outside on extraction."""
    _place(media_root, "one.pdf")
    _place(media_root, "two.pdf")
    hostile = record(OPEN_FILE, "one.pdf")
    hostile["name"] = "../../../../etc/cron.d/payload"
    mongo.records[OPEN_FILE] = hostile
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "two.pdf")
    subject = resource(
        filesObj=[{"id": OPEN_FILE, "order": 0}, {"id": RESERVED_FILE, "order": 1}]
    )

    response = files.bulk_download(subject, "original", "alice")

    with zipfile.ZipFile(response.path) as archive:
        for name in archive.namelist():
            assert ".." not in name
            assert not name.startswith("/")


def test_a_single_file_is_served_directly_rather_than_zipped(mongo, media_root):
    _place(media_root, "one.pdf")
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "one.pdf")

    response = files.bulk_download(resource(), "original", "alice")

    assert response.path.suffix == ".pdf"


def test_a_resource_whose_files_are_all_restricted_is_a_404(mongo, media_root):
    mongo.records[RESERVED_FILE] = record(RESERVED_FILE, "secret.pdf", access_rights="reserved")
    subject = resource(filesObj=[{"id": RESERVED_FILE, "order": 0}])

    with pytest.raises(files.DownloadRefused) as exc:
        files.bulk_download(subject, "original", None, public=True)

    assert exc.value.status_code == 404


def test_downloads_disabled_refuses_the_public_route_too(mongo, media_root, monkeypatch):
    """The legacy public route omitted the capability check entirely."""
    monkeypatch.setattr("archihub.api.records.media.downloads_enabled", lambda: False)
    mongo.records[OPEN_FILE] = record(OPEN_FILE, "one.pdf")

    with pytest.raises(files.DownloadRefused) as exc:
        files.bulk_download(resource(), "original", None, public=True)

    assert exc.value.status_code == 400


def test_stale_archives_are_swept(tmp_path):
    import os
    import time

    stale = tmp_path / f"{files.ZIP_PREFIX}old.zip"
    fresh = tmp_path / f"{files.ZIP_PREFIX}new.zip"
    other = tmp_path / "someone-elses.zip"
    for path in (stale, fresh, other):
        path.write_bytes(b"x")
    old = time.time() - files.STALE_ZIP_SECONDS - 60
    os.utime(stale, (old, old))
    os.utime(other, (old, old))

    assert files.sweep_stale_archives(tmp_path) == 1
    assert not stale.exists()
    assert fresh.exists()
    assert other.exists()


# ---------------------------------------------------------------------------
# The public rule
# ---------------------------------------------------------------------------


def test_a_published_unrestricted_resource_is_public(mongo):
    assert access.is_public(resource()) is True


def test_a_draft_is_not_public(mongo):
    assert access.is_public(resource(status="draft")) is False


def test_a_restricted_type_is_omitted_from_a_listing_not_refused(mongo, monkeypatch):
    """The original answered 401 for the whole request if any type was restricted.

    One restricted type in a saved view blanked the entire public browse page.
    """
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.type_roles",
        lambda slug: {"viewRoles": ["editor"] if slug == "reserved" else []},
    )

    assert public.public_types(["carpeta", "reserved"]) == ["carpeta"]


def test_a_listing_of_only_restricted_types_is_empty_not_an_error(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.type_roles", lambda slug: {"viewRoles": ["editor"]}
    )

    payload, status = public.get_all({"post_type": ["reserved"]})

    assert (payload, status) == ({"total": 0, "resources": []}, 200)


@pytest.mark.parametrize("view", [None, "", "nonsense", "TREE"])
def test_an_unrecognised_tree_view_is_a_400_not_an_empty_500(mongo, view):
    """The original had no `else` and returned None, which Flask could not render."""
    payload, status = public.get_tree({"view": view} if view is not None else {})

    assert status == 400


def test_a_non_public_resource_is_a_404(mongo):
    mongo.resources[RESOURCE_ID] = resource(status="draft")

    payload, status = public.get_by_id(RESOURCE_ID)

    assert status == 404
    assert payload == {"msg": "Resource does not exist"}


def test_a_missing_resource_returns_the_identical_response(mongo):
    missing = public.get_by_id("000000000000000000000000")
    mongo.resources[RESOURCE_ID] = resource(status="draft")

    assert public.get_by_id(RESOURCE_ID) == missing


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def form(*fields):
    return {"fields": list(fields)}


def test_a_text_field_is_rendered_with_its_label(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form({"destiny": "metadata.firstLevel.title", "type": "text", "label": "Title"}),
    )

    rendered = presentation.build_fields(resource(), "alice")

    assert rendered == [
        {"label": "Title", "value": "A folder", "type": "text", "isTitle": True}
    ]


def test_a_field_with_no_value_is_left_out(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form({"destiny": "metadata.firstLevel.scope", "type": "text", "label": "Scope"}),
    )

    assert presentation.build_fields(resource(), "alice") == []


def test_files_and_separators_are_never_rendered(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form(
            {"destiny": "metadata.firstLevel.files", "type": "file", "label": "Files"},
            {"destiny": "metadata.firstLevel.sep", "type": "separator", "label": "—"},
        ),
    )

    assert presentation.build_fields(resource(), "alice") == []


def test_a_restricted_field_is_replaced_not_omitted(mongo, monkeypatch):
    """The interface shows that something exists and is not for you."""
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form(
            {
                "destiny": "metadata.firstLevel.title",
                "type": "text",
                "label": "Title",
                "accessRights": ["reserved"],
            }
        ),
    )

    rendered = presentation.build_fields(resource(), "alice")

    assert rendered[0]["value"] == "You don't have the required authorization"


def test_a_restricted_field_is_always_hidden_from_an_anonymous_caller(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form(
            {
                "destiny": "metadata.firstLevel.title",
                "type": "text",
                "label": "Title",
                "accessRights": ["reserved"],
            }
        ),
    )

    rendered = presentation.build_fields(resource(), None, public=True)

    assert rendered[0]["value"] == "You don't have the required authorization"


def test_an_author_field_is_rendered_as_a_plain_name(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form({"destiny": "metadata.firstLevel.author", "type": "author", "label": "Author"}),
    )
    subject = resource()
    subject["metadata"]["firstLevel"]["author"] = ["García|Gabriel", "Borges, Jorge"]

    rendered = presentation.build_fields(subject, "alice")

    assert rendered[0]["value"] == ["García Gabriel", "Borges Jorge"]


def test_a_repeater_row_missing_a_subfield_does_not_raise(mongo, monkeypatch):
    """The original subscripted the subfield directly, so a row saved before the
    subfield was added to the form raised KeyError on read."""
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form(
            {
                "destiny": "metadata.firstLevel.notes",
                "type": "repeater",
                "label": "Notes",
                "subfields": [
                    {"destiny": "who", "type": "text", "name": "Who"},
                    {"destiny": "added_later", "type": "text", "name": "Later"},
                ],
            }
        ),
    )
    subject = resource()
    subject["metadata"]["firstLevel"]["notes"] = [{"who": "a cataloguer"}]

    rendered = presentation.build_fields(subject, "alice")

    assert rendered[0]["value"] == [[{"label": "Who", "value": "a cataloguer", "type": "text"}]]


def test_a_relation_to_a_deleted_resource_is_dropped_not_raised(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: form({"destiny": "metadata.firstLevel.related", "type": "relation", "label": "Related"}),
    )
    subject = resource()
    subject["metadata"]["firstLevel"]["related"] = [
        {"id": "000000000000000000000000", "post_type": "carpeta"}
    ]

    rendered = presentation.build_fields(subject, "alice")

    assert rendered[0]["value"] == []


def test_a_dangling_ancestor_keeps_its_entry_without_a_name(mongo):
    """The original subscripted a lookup it never checked and raised TypeError."""
    described = presentation.describe_parents([{"id": "000000000000000000000000"}])

    assert described == [{"id": "000000000000000000000000"}]


def test_the_files_tab_is_prepended_when_a_resource_has_files(mongo, monkeypatch):
    monkeypatch.setattr("archihub.api.types.services.get_metadata", lambda slug: form())
    mongo.settings["post_types_settings"] = {
        "data": [{"id": "tipos_vista_individual", "value": ["carpeta"]}]
    }

    described = presentation.describe(resource(), "alice")

    assert described["children"][0]["slug"] == "files"
    assert described["files"] == 1


def test_a_resource_with_no_files_reports_none(mongo, monkeypatch):
    monkeypatch.setattr("archihub.api.types.services.get_metadata", lambda slug: form())

    described = presentation.describe(resource(filesObj=[]), "alice")

    assert described["files"] is None
