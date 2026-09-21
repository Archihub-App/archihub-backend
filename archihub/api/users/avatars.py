"""Profile photographs: accepting one, storing it, serving it.

An avatar is the one file this application renders into a browser from an
ANONYMOUS request, so it is the one upload whose content matters as much as its
size. Three rules follow, and they are enforced here rather than left to the
caller:

* The bytes are decoded and RE-ENCODED before anything is stored. A file that is
  not really an image cannot survive that, so nothing a browser might interpret
  as a document is ever served from this origin - and the metadata a camera
  writes, GPS coordinates among it, does not survive it either.
* The stored name is generated here, never the uploader's, and carries the
  extension of what was actually WRITTEN. The media type is then read from a
  two-entry map rather than guessed from anything a client sent.
* Replacing an avatar deletes the file it replaces. Anyone holding a name can
  read it without signing in, so a superseded photograph left on disk stays
  readable indefinitely.

The output is bounded to a square that comfortably covers a profile header, so
what is stored is a thumbnail rather than whatever came off a phone.
"""

from __future__ import annotations

import logging
from pathlib import Path

from archihub.core import files as filestore
from archihub.core.files import UnsupportedFile
from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

#: Below the general upload ceiling on purpose: this is a profile photograph,
#: and the ceiling that applies to archival masters is the wrong one for it.
MAX_AVATAR_BYTES = 5 * 1024 * 1024

#: What an uploader may claim, checked before a byte is read.
ACCEPTED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})

#: What the bytes must actually be, checked after they are.
ACCEPTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")

#: What is WRITTEN, and therefore every media type this directory can serve.
#: Serving reads this map, so the set of types a browser can be handed from here
#: is fixed by what the re-encoder is able to produce.
SERVED_MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png"}

#: The longest edge of a stored avatar.
AVATAR_PIXELS = 512

#: A source larger than this is refused before it is decoded. An image can
#: declare enormous dimensions in a few compressed bytes, so a size ceiling
#: alone does not bound the memory a decode needs.
MAX_SOURCE_PIXELS = 50_000_000

DIRECTORY = "avatars"
_STAGING = ".incoming"


def _root() -> Path:
    """Where avatars live, under the tree that already serves web derivatives.

    Not the user-files tree: that holds per-user plugin output reachable only by
    its owner, and these are read anonymously.
    """
    root = get_settings().web_files_path
    if not root:
        raise UnsupportedFile(_("This instance has no file storage configured"))
    return Path(root) / DIRECTORY


def url_for(filename: str) -> str:
    """The path a browser fetches an avatar from.

    Root-relative rather than absolute: the frontend already prefixes the API
    origin it was configured with, and an absolute URL would bake in the host a
    single deployment happened to be reachable at when the file was stored.
    """
    return f"/users/avatar/{filename}"


def path_for(filename: str) -> Path:
    """The file a served name refers to, or raise.

    The name reaches this from a URL, so it is required to be a bare filename
    with an extension this directory can serve, and is resolved inside the root
    rather than joined to it.
    """
    if not filename or "/" in filename or "\\" in filename or filename != Path(filename).name:
        raise UnsupportedFile(_("Invalid file path"))

    if filestore.extension_of(filename) not in SERVED_MEDIA_TYPES:
        raise UnsupportedFile(_("Invalid file path"))

    return filestore.resolve_within(_root(), filename)


def media_type_for(filename: str) -> str:
    """The type a stored avatar is served as, from its written extension."""
    return SERVED_MEDIA_TYPES[filestore.extension_of(filename)]


def store(source, original_filename: str) -> str:
    """Re-encode an uploaded image into the avatar directory; return its name.

    ``source`` is a readable binary file-like object - ``UploadFile.file`` in
    practice. Raises ``UploadTooLarge`` or ``UnsupportedFile``; on either, and on
    a decode that fails, nothing is left behind.
    """
    if not filestore.is_allowed(original_filename, ACCEPTED_EXTENSIONS):
        raise UnsupportedFile(_("Only PNG, JPG and WEBP images are accepted"))

    root = _root()
    staged = filestore.store_upload(
        source, root / _STAGING, original_filename, max_bytes=MAX_AVATAR_BYTES
    )

    try:
        if not filestore.content_matches_extension(staged.path, ACCEPTED_MEDIA_TYPES):
            raise UnsupportedFile(_("Only PNG, JPG and WEBP images are accepted"))
        return _reencode(staged.path, root)
    finally:
        # The upload itself is never kept: what is served is the re-encoded
        # copy, and this one still carries whatever the uploader sent.
        filestore.remove_quietly(staged.path)


def remove(filename: str | None) -> None:
    """Delete a stored avatar, if there is one and it is ours."""
    if not filename:
        return
    try:
        filestore.remove_quietly(path_for(filename))
    except Exception:
        logger.warning("Could not remove a superseded avatar", exc_info=True)


def _reencode(source: Path, root: Path) -> str:
    """Decode, orient, shrink and write a fresh image; return its stored name.

    Alpha decides the output format, because flattening it would put a white
    square behind a logo that was uploaded to sit on the page background.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(source) as opened:
            # `open` reads the header only, so the declared size is known before
            # anything is decoded and a bomb is refused rather than expanded.
            width, height = opened.size
            if width * height > MAX_SOURCE_PIXELS:
                raise UnsupportedFile(_("That image is too large to process"))

            keep_alpha = opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info
            oriented = ImageOps.exif_transpose(opened) or opened
            image = oriented.convert("RGBA" if keep_alpha else "RGB")
            image.thumbnail((AVATAR_PIXELS, AVATAR_PIXELS), Image.Resampling.LANCZOS)

            extension = "png" if keep_alpha else "jpg"
            destination = root / filestore.unique_name(f"avatar.{extension}")
            destination.parent.mkdir(parents=True, exist_ok=True)

            partial = destination.with_suffix(destination.suffix + ".partial")
            try:
                if extension == "png":
                    image.save(partial, format="PNG", optimize=True)
                else:
                    image.save(partial, format="JPEG", quality=88, optimize=True)
                partial.replace(destination)
            except Exception:
                # Moved into place only once it is whole, so a failed write
                # cannot leave a truncated file under a name that is now stored
                # on the account and served to anyone who asks.
                filestore.remove_quietly(partial)
                raise
    except UnidentifiedImageError:
        raise UnsupportedFile(_("Only PNG, JPG and WEBP images are accepted")) from None

    return destination.name
