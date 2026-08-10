"""Turning a snap's coordinates back into something you can look at.

Nothing here is stored. A snap records where in a record it points, and this
crops or cuts that region out of the source on every read - so a snap stays
correct when the derivative behind it is regenerated, and it never holds a copy
of restricted content in its own right.

**THE RECORD'S ACCESS RULE GOVERNS, ALWAYS.** Every function takes an
already-loaded record, and the only way to get one is through
``records.services.load_visible`` or ``records.public.load_public``. That is
deliberate: the caller cannot skip the check by holding a snap, because the
snap does not carry the pixels.
"""

from __future__ import annotations

import logging

from archihub.api.records import media, viewers
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

#: Quality for the cropped JPEG. The original's value, kept: these are working
#: references, not preservation copies.
CROP_QUALITY = 70

#: Derivative a document page is cropped from. Fixed, not client-selected -
#: see the ``viewers`` module docstring for why that matters.
DOCUMENT_PAGE_SIZE = "big"


class RenderFailed(Exception):
    """The snap cannot be rendered, with the status to answer."""

    def __init__(self, message: str, status_code: int = 404) -> None:
        super().__init__(message)
        self.status_code = status_code


def render(snap: dict, record: dict):
    """A response showing this snap: a cropped JPEG, or a media fragment."""
    snap_type = snap.get("type")

    if snap_type == "document":
        return _crop_response(_document_page(snap, record), snap["data"]["bbox"])
    if snap_type == "image":
        return _crop_response(_image_file(record), snap["data"]["bbox"])
    if snap_type in ("audio", "video"):
        return _fragment(snap, record)

    raise RenderFailed(_("Unsupported snap type"), 400)


# ---------------------------------------------------------------------------
# Still images
# ---------------------------------------------------------------------------


def _document_page(snap: dict, record: dict):
    """The rendered page a document snap points at, as bytes.

    Reuses the page viewer rather than reaching into the filesystem, so the
    ``size`` allowlist and the media-root containment check apply here too.
    """
    import base64

    page = snap["data"]["page"]
    try:
        pages = viewers.page_images(record, [page - 1], DOCUMENT_PAGE_SIZE)
    except viewers.ViewerError as exc:
        raise RenderFailed(str(exc), exc.status_code) from None

    if not pages:
        raise RenderFailed(_("Record does not have that many pages"), 404)

    return base64.b64decode(pages[0]["data"])


def _image_file(record: dict):
    """The large derivative of an image record, as bytes."""
    try:
        path, kind = media.derivative_of(record, "large")
    except media.NotStreamable as exc:
        raise RenderFailed(str(exc), 404) from None

    if kind != "image":
        raise RenderFailed(_("File not found"), 404)
    if not path.is_file():
        raise RenderFailed(_("File not found"), 404)

    return path.read_bytes()


def _crop_response(source: bytes, box: dict):
    """Crop the fractional box out of an image and serve it as a JPEG."""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    from archihub.core.responses import bytes_response

    try:
        image = Image.open(BytesIO(source))
    except UnidentifiedImageError:
        logger.warning("A snap points at something that is not a decodable image")
        raise RenderFailed(_("File not found"), 404) from None

    width, height = image.size
    left = width * box["x"]
    top = height * box["y"]
    right = width * (box["x"] + box["width"])
    bottom = height * (box["y"] + box["height"])

    # The box is validated at creation, but a snap made before that validation
    # existed can still be stored, so the crop is clamped rather than trusted.
    left, top = max(0, int(left)), max(0, int(top))
    right, bottom = min(width, int(right)), min(height, int(bottom))
    if right <= left or bottom <= top:
        raise RenderFailed(_("bbox is outside the image"), 400)

    cropped = image.crop((left, top, right, bottom))
    if cropped.mode not in ("RGB", "L"):
        # A PNG page with transparency cannot be saved as JPEG otherwise.
        cropped = cropped.convert("RGB")

    buffer = BytesIO()
    cropped.save(buffer, "JPEG", quality=CROP_QUALITY)
    return bytes_response(buffer.getvalue(), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Media fragments
# ---------------------------------------------------------------------------


def _fragment(snap: dict, record: dict):
    data = snap["data"]
    try:
        return media.stream_fragment(record, (data["begin"], data["end"]))
    except media.NotStreamable as exc:
        raise RenderFailed(str(exc), 400) from None
    except media.FragmentFailed as exc:
        raise RenderFailed(str(exc), 500) from None
