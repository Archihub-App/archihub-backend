"""The profile contract: personal fields, catalogue counters, and the avatar.

Three things here are easy to get wrong in a way nothing reports.

``created_at`` and ``createdAt`` are encoded DIFFERENTLY on purpose - a plain
ISO string beside the wrapped ``{"$date": ...}`` form the rest of this API uses.
The interface reads the first with ``new Date(...)``, which accepts the wrapped
object without complaint and yields an invalid date from it, so getting this
wrong is a 200 carrying a complete profile that renders "Invalid Date".

The display name has to follow the two halves the profile screen edits, because
``name`` is what the user listing sorts by and what every resource this person
catalogues records as its cataloguer.

And an avatar is served to ANONYMOUS browsers, so what is stored has to be an
image and nothing else. The uploaded bytes are re-encoded rather than inspected
and kept.
"""

from __future__ import annotations

import datetime
import io

import pytest

from archihub.api.users import avatars, services
from archihub.core.files import UnsupportedFile, UploadTooLarge


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, user: dict | None = None, resources: list | None = None):
        self.user = user
        self.resources = resources or []
        self.updates: list[tuple] = []
        self.fail_update = False

    def get_record(self, collection, filters, fields=None):
        return self.user

    def update_record(self, collection, filters, update):
        if self.fail_update:
            raise RuntimeError("mongo is down")
        self.updates.append((collection, filters, update))
        (self.user or {}).update(update)

    def _matching(self, filters):
        rows = []
        for row in self.resources:
            if row.get("createdBy") != filters.get("createdBy"):
                continue
            status = filters.get("status")
            if isinstance(status, dict) and "$ne" in status and row.get("status") == status["$ne"]:
                continue
            rows.append(row)
        return rows

    def count(self, collection, filters=None):
        return len(self._matching(filters or {}))

    def distinct(self, collection, field, filters=None):
        return sorted({row.get(field) for row in self._matching(filters or {})})


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


@pytest.fixture
def avatar_root(tmp_path, monkeypatch):
    class Settings:
        web_files_path = str(tmp_path)

    monkeypatch.setattr(avatars, "get_settings", lambda: Settings())
    return tmp_path / avatars.DIRECTORY


def image_bytes(mode="RGB", size=(1200, 900), fmt="JPEG", exif=None):
    from PIL import Image

    buffer = io.BytesIO()
    colour = (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)
    image = Image.new(mode, size, colour)
    image.save(buffer, format=fmt, **({"exif": exif} if exif else {}))
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# The profile shape
# ---------------------------------------------------------------------------


def test_every_promised_field_is_present_on_an_account_that_predates_them(mongo):
    """An older account must not be missing fields the contract declares.

    A consumer cannot tell "this person has no telephone number" from "this
    server does not have that field": the first renders blank, the second
    throws.
    """
    mongo.user = {"_id": "1", "username": "alice", "createdAt": datetime.datetime(2024, 3, 15)}

    payload, status = services.get_profile("alice")

    assert status == 200
    for field in ("first_name", "last_name", "phone", "avatar_url", "created_at"):
        assert field in payload


def test_created_at_is_a_plain_iso_string(mongo):
    """Not the wrapped form. `new Date({"$date": ...})` is an invalid date."""
    mongo.user = {"_id": "1", "username": "alice",
                  "createdAt": datetime.datetime(2024, 3, 15, 10, 30)}

    payload, _status = services.get_profile("alice")

    assert payload["created_at"] == "2024-03-15T10:30:00"
    assert isinstance(payload["created_at"], str)


def test_created_at_does_not_replace_the_camel_case_field(mongo):
    """`createdAt` is what this route has always answered with."""
    mongo.user = {"_id": "1", "username": "alice",
                  "createdAt": datetime.datetime(2024, 3, 15, 10, 30)}

    payload, _status = services.get_profile("alice")

    assert payload["createdAt"] == {"$date": "2024-03-15T10:30:00Z"}


def test_a_missing_creation_date_does_not_break_the_profile(mongo):
    mongo.user = {"_id": "1", "username": "alice"}

    payload, status = services.get_profile("alice")

    assert status == 200
    assert payload["created_at"] is None


def test_stats_are_absent_unless_asked_for(mongo):
    """`/users/compromise` reads the same profile and needs no counters."""
    mongo.user = {"_id": "1", "username": "alice"}

    payload, _status = services.get_profile("alice")

    assert "stats" not in payload


def test_stats_are_present_when_asked_for(mongo):
    mongo.user = {"_id": "1", "username": "alice"}
    mongo.resources = [{"createdBy": "alice", "status": "published", "post_type": "fonds"}]

    payload, _status = services.get_profile("alice", with_stats=True)

    assert payload["stats"] == {"records_created": 1, "collections_count": 1}


# ---------------------------------------------------------------------------
# The counters
# ---------------------------------------------------------------------------


def test_the_counters_count_only_this_account(mongo):
    mongo.resources = [
        {"createdBy": "alice", "status": "published", "post_type": "fonds"},
        {"createdBy": "bob", "status": "published", "post_type": "series"},
    ]

    assert services.profile_stats("alice") == {"records_created": 1, "collections_count": 1}


def test_the_recycle_bin_does_not_count(mongo):
    """Deleting your own work does not make you its author twice."""
    mongo.resources = [
        {"createdBy": "alice", "status": "published", "post_type": "fonds"},
        {"createdBy": "alice", "status": "deleted", "post_type": "series"},
    ]

    assert services.profile_stats("alice") == {"records_created": 1, "collections_count": 1}


def test_collections_are_counted_once_each(mongo):
    mongo.resources = [
        {"createdBy": "alice", "status": "published", "post_type": "fonds"},
        {"createdBy": "alice", "status": "published", "post_type": "fonds"},
        {"createdBy": "alice", "status": "draft", "post_type": "series"},
    ]

    assert services.profile_stats("alice") == {"records_created": 3, "collections_count": 2}


def test_counters_that_cannot_be_read_do_not_take_the_profile_with_them(monkeypatch):
    """These are the only part of a profile that queries another collection.

    A profile that will not open is worse than one whose counters read zero.
    """
    class Broken:
        def count(self, *args, **kwargs):
            raise RuntimeError("resources is unreachable")

        def distinct(self, *args, **kwargs):
            raise RuntimeError("resources is unreachable")

    monkeypatch.setattr(services, "_mongo", lambda: Broken())

    assert services.profile_stats("alice") == {"records_created": 0, "collections_count": 0}


# ---------------------------------------------------------------------------
# What must never be published
# ---------------------------------------------------------------------------


def test_the_stored_avatar_filename_is_never_returned(mongo):
    """`avatar` is bookkeeping - what replacing one has to delete.

    Only `avatar_url` is a client's business. Asserted through the projection
    the profile actually asks for, because that is what decides it.
    """
    captured = {}

    def get_record(collection, filters, fields=None):
        captured["fields"] = fields
        return {"_id": "1", "username": "alice"}

    mongo.get_record = get_record
    services.get_profile("alice")

    assert captured["fields"].get("avatar") == 0
    assert captured["fields"].get("password") == 0


def test_the_listing_publishes_no_telephone_number():
    """Every editor can read the user listing; a phone number is personal."""
    assert services._LIST_PROJECTION.get("phone") == 0
    assert services._LIST_PROJECTION.get("avatar") == 0


def test_the_admin_detail_lookup_publishes_no_stored_filename():
    assert services._DETAIL_PROJECTION.get("avatar") == 0


# ---------------------------------------------------------------------------
# Editing the profile
# ---------------------------------------------------------------------------


@pytest.fixture
def account(mongo, monkeypatch):
    import bcrypt

    mongo.user = {
        "username": "alice",
        "password": bcrypt.hashpw(b"right", bcrypt.gensalt()).decode(),
        "name": "alice",
    }
    return mongo


def test_the_new_fields_are_stored(account):
    payload, status = services.update_me(
        {"password": "right", "first_name": "Pedro Néstor", "last_name": "Gómez",
         "phone": "+57 300 1234567"},
        "alice",
    )

    assert status == 200, payload
    stored = account.updates[0][2]
    assert stored["first_name"] == "Pedro Néstor"
    assert stored["last_name"] == "Gómez"
    assert stored["phone"] == "+57 300 1234567"


def test_the_display_name_follows_the_two_halves(account):
    """`name` is what the listing sorts by and what a resource records.

    Left to drift it stays whatever the account was created with, and every
    screen that names this person goes on showing it.
    """
    _payload, status = services.update_me(
        {"password": "right", "first_name": "Pedro", "last_name": "Gómez"}, "alice"
    )

    assert status == 200
    assert account.updates[0][2]["name"] == "Pedro Gómez"


def test_an_explicit_name_is_not_overwritten_by_the_derived_one(account):
    _payload, status = services.update_me(
        {"password": "right", "first_name": "Pedro", "last_name": "Gómez",
         "name": "Pedro N. Gómez"},
        "alice",
    )

    assert status == 200
    assert account.updates[0][2]["name"] == "Pedro N. Gómez"


def test_only_a_surname_still_produces_a_name(account):
    services.update_me({"password": "right", "last_name": "Gómez"}, "alice")
    assert account.updates[0][2]["name"] == "Gómez"


def test_a_field_that_was_not_sent_is_left_alone(account):
    account.user["phone"] = "+57 300 1234567"

    services.update_me({"password": "right", "first_name": "Pedro"}, "alice")

    assert "phone" not in account.updates[0][2]


def test_an_over_long_value_is_refused_rather_than_truncated(account):
    payload, status = services.update_me(
        {"password": "right", "first_name": "x" * 200}, "alice"
    )

    assert status == 400
    assert account.updates == []


def test_a_value_that_is_not_text_is_refused(account):
    _payload, status = services.update_me(
        {"password": "right", "phone": {"$ne": None}}, "alice"
    )

    assert status == 400
    assert account.updates == []


def test_the_wrong_password_changes_nothing(account):
    _payload, status = services.update_me(
        {"password": "wrong", "first_name": "Pedro"}, "alice"
    )

    assert status == 400
    assert account.updates == []


def test_a_name_pasted_with_a_newline_is_stored_as_one_line(account):
    services.update_me({"password": "right", "first_name": "Pedro\n"}, "alice")
    assert account.updates[0][2]["first_name"] == "Pedro"


def test_the_self_update_schema_ignores_privilege_fields():
    """This is the one write a user makes to their own account."""
    from archihub.api.users.schemas import SelfUpdateRequest

    parsed = SelfUpdateRequest(
        password="x", first_name="Pedro",
        roles=[{"id": "admin"}], accessRights=[{"id": "all"}], verified=True,
        avatar="../../etc/passwd", avatar_url="http://elsewhere/x.png",
    ).model_dump(exclude_unset=True)

    assert set(parsed) == {"password", "first_name"}


# ---------------------------------------------------------------------------
# Avatars: what may be stored
# ---------------------------------------------------------------------------


def test_a_real_photograph_is_stored_and_shrunk(avatar_root):
    from PIL import Image

    name = avatars.store(image_bytes(size=(1600, 1200)), "MyPhoto.JPG")

    with Image.open(avatars.path_for(name)) as stored:
        assert stored.format == "JPEG"
        assert max(stored.size) == avatars.AVATAR_PIXELS


def test_transparency_survives_as_a_png(avatar_root):
    from PIL import Image

    name = avatars.store(image_bytes("RGBA", (400, 400), "PNG"), "logo.png")

    with Image.open(avatars.path_for(name)) as stored:
        assert stored.format == "PNG"
        assert stored.mode == "RGBA"


def test_a_document_wearing_an_image_name_is_refused(avatar_root):
    """The bytes decide, not the name.

    This directory is read by anonymous browsers from the API's own origin, so a
    file a browser would treat as a document must never reach it.
    """
    with pytest.raises(UnsupportedFile):
        avatars.store(io.BytesIO(b"<html><script>alert(1)</script></html>"), "x.png")


def test_a_format_outside_the_accepted_set_is_refused_on_its_content(avatar_root):
    """The accepted formats are enforced against the BYTES, not just the name.

    A GIF is a real image that the decoder is perfectly happy to open, so
    nothing downstream would refuse it - only the content check names which
    formats this endpoint accepts.
    """
    with pytest.raises(UnsupportedFile):
        avatars.store(image_bytes(size=(40, 40), fmt="GIF"), "animation.png")


def test_an_image_under_an_unaccepted_name_is_refused(avatar_root):
    with pytest.raises(UnsupportedFile):
        avatars.store(image_bytes(), "drawing.svg")


def test_a_file_over_the_ceiling_is_refused(avatar_root):
    oversized = io.BytesIO(b"\xff\xd8\xff" + b"0" * (avatars.MAX_AVATAR_BYTES + 1))

    with pytest.raises(UploadTooLarge):
        avatars.store(oversized, "big.jpg")


def test_an_image_declaring_enormous_dimensions_is_refused_before_it_is_decoded(
    avatar_root, monkeypatch
):
    """A decompression bomb is small on disk and vast in memory."""
    monkeypatch.setattr(avatars, "MAX_SOURCE_PIXELS", 100)

    with pytest.raises(UnsupportedFile):
        avatars.store(image_bytes(size=(200, 200)), "bomb.png")


def test_a_refused_upload_leaves_nothing_behind(avatar_root):
    for source, name in (
        (io.BytesIO(b"not an image"), "x.png"),
        (io.BytesIO(b"\xff\xd8\xff" + b"0" * (avatars.MAX_AVATAR_BYTES + 1)), "big.jpg"),
    ):
        with pytest.raises((UnsupportedFile, UploadTooLarge)):
            avatars.store(source, name)

    staged = avatar_root / "_STAGING"
    assert not list(avatar_root.glob("*.jpg"))
    assert not list(avatar_root.glob("*.png"))
    assert not staged.exists()
    incoming = avatar_root / avatars._STAGING
    assert not incoming.exists() or not list(incoming.iterdir())


def test_the_camera_metadata_does_not_survive(avatar_root):
    """An uploaded photograph can carry GPS coordinates and a serial number."""
    from PIL import Image

    exif = Image.new("RGB", (10, 10)).getexif()
    exif[0x8825] = {}          # GPS
    exif[0xA431] = "SERIAL123"  # body serial number

    name = avatars.store(image_bytes(exif=exif.tobytes()), "gps.jpg")

    with Image.open(avatars.path_for(name)) as stored:
        assert dict(stored.getexif()) == {}


def test_the_stored_name_is_not_the_uploaded_one(avatar_root):
    name = avatars.store(image_bytes(), "../../etc/passwd.jpg")

    assert "passwd" not in name
    assert name == avatars.path_for(name).name


# ---------------------------------------------------------------------------
# Avatars: what may be served
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "sub/dir.jpg",
        "drawing.svg",
        "noextension",
        "",
        ".incoming",
    ],
)
def test_a_name_this_directory_does_not_write_is_refused(avatar_root, name):
    """The name arrives in a URL. Only a bare filename of a written type."""
    with pytest.raises(UnsupportedFile):
        avatars.path_for(name)


def test_the_served_type_comes_from_the_written_extension(avatar_root):
    assert avatars.media_type_for("abc.jpg") == "image/jpeg"
    assert avatars.media_type_for("abc.png") == "image/png"


def test_only_types_the_re_encoder_produces_can_be_served():
    """Serving reads this map, so it bounds what a browser can be handed."""
    assert set(avatars.SERVED_MEDIA_TYPES) == {"jpg", "png"}
    assert all(t.startswith("image/") for t in avatars.SERVED_MEDIA_TYPES.values())


def test_the_url_is_root_relative(avatar_root):
    url = avatars.url_for("abc.jpg")

    assert url.startswith("/users/avatar/")
    assert "://" not in url


# ---------------------------------------------------------------------------
# Avatars: the account
# ---------------------------------------------------------------------------


def test_replacing_a_photograph_deletes_the_one_it_replaces(mongo, avatar_root):
    """Anyone holding the name can read it without signing in."""
    mongo.user = {"username": "alice"}

    first, _status = services.set_avatar("alice", image_bytes(), "a.jpg")
    old = avatars.path_for(first["avatar_url"].rsplit("/", 1)[-1])
    assert old.exists()

    second, _status = services.set_avatar("alice", image_bytes(), "b.jpg")

    assert not old.exists()
    assert avatars.path_for(second["avatar_url"].rsplit("/", 1)[-1]).exists()


def test_a_stored_photograph_that_cannot_be_recorded_is_removed(mongo, avatar_root):
    """Nothing may be left on disk that no account refers to."""
    mongo.user = {"username": "alice"}
    mongo.fail_update = True

    with pytest.raises(RuntimeError):
        services.set_avatar("alice", image_bytes(), "a.jpg")

    assert not list(avatar_root.glob("*.jpg"))


def test_removing_a_photograph_clears_both_halves(mongo, avatar_root):
    mongo.user = {"username": "alice"}
    services.set_avatar("alice", image_bytes(), "a.jpg")
    stored = avatars.path_for(mongo.user["avatar_url"].rsplit("/", 1)[-1])

    payload, status = services.clear_avatar("alice")

    assert status == 200
    assert payload["avatar_url"] is None
    assert mongo.user["avatar"] is None and mongo.user["avatar_url"] is None
    assert not stored.exists()


def test_removing_a_photograph_from_an_account_without_one_is_not_an_error(
    mongo, avatar_root
):
    mongo.user = {"username": "alice"}

    _payload, status = services.clear_avatar("alice")

    assert status == 200
