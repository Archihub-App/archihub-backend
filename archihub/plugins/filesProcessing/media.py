"""Deriving web-ready versions of an archived file.

One function per media kind. What differs between them (the tool, the arguments,
the outputs) is data below; the error handling is shared.

A failure is logged with its traceback and raised as ``ProcessingFailed``
carrying only what the file was, never an underlying exception's text, which
would send a path on the server's disk to whoever reads the job list.

Every external tool runs with a timeout, output captured rather than inherited,
and a non-zero exit treated as failure - the same guards as ``records/media.py``.
A LibreOffice waiting on a dialog for a malformed document cannot hold a Celery
worker indefinitely.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class ProcessingFailed(RuntimeError):
    """A derivative could not be produced."""


#: LibreOffice conversions are minutes on a large document and never hours.
LIBREOFFICE_TIMEOUT = 900

#: Beyond this dimension an image gets deep-zoom tiles as well as flat versions.
DZI_THRESHOLD = 4096

#: (suffix, longest edge, JPEG quality) for the flat image derivatives.
IMAGE_SIZES = (("_large", 2500, 90), ("_medium", 1100, 80), ("_small", 110, 80))

#: Camera RAW formats. libvips has no RAW decoder: it opens most of these as the
#: TIFF container they are, finds only the embedded preview strips, and fails.
#: They are demosaiced from the sensor data by LibRaw instead.
RAW_EXTENSIONS = frozenset({".cr2", ".cr3", ".arw", ".srf", ".nef", ".nrw", ".dng"})

#: Rows a CSV/Excel preview keeps. The viewer paginates beyond this from the
#: full copy; the preview exists so opening a 400MB spreadsheet is not a
#: 400MB download.
PREVIEW_ROWS = 99


def audio(source: Path, output_stem: Path) -> bool:
    """MP3 and Ogg Vorbis versions of an audio master."""
    import ffmpeg

    try:
        ffmpeg.input(str(source)).output(
            f"{output_stem}.mp3", acodec="libmp3lame", ab="128k"
        ).overwrite_output().run(quiet=True)

        ffmpeg.input(str(source)).output(
            f"{output_stem}.ogg", acodec="libvorbis", **{"q:a": 4}
        ).overwrite_output().run(quiet=True)
    except Exception as exc:
        raise ProcessingFailed(f"Could not derive audio from {source.name}") from exc

    return True


def video(source: Path, output_stem: Path) -> tuple[bool, bool]:
    """MP4 + WebM if there is a video stream, MP3 + Ogg if it is audio only.

    Returns ``(has_audio, has_video)``.
    """
    import ffmpeg

    has_video, has_audio = _streams(source)

    try:
        if has_video:
            for extension, video_codec, audio_codec in (
                ("mp4", "libx264", "aac"),
                ("webm", "libvpx", "libvorbis"),
            ):
                ffmpeg.input(str(source)).output(
                    f"{output_stem}.{extension}",
                    vcodec=video_codec,
                    acodec=audio_codec,
                    vf="scale=480:trunc(ow/a/2)*2",
                ).overwrite_output().run(quiet=True)
        elif has_audio:
            audio(source, output_stem)
    except ProcessingFailed:
        raise
    except Exception as exc:
        raise ProcessingFailed(f"Could not derive video from {source.name}") from exc

    return has_audio, has_video


def _streams(source: Path) -> tuple[bool, bool]:
    """``(has_video, has_audio)`` for a media file.

    Probing failures are assumed to mean video: the remaining path transcodes
    both, so guessing wrong costs time rather than correctness.
    """
    try:
        import ffprobe3

        metadata = ffprobe3.FFProbe(str(source))
    except Exception:
        logger.warning("Could not probe %s; assuming it has video", source.name)
        return True, False

    has_video = any(stream.is_video() for stream in metadata.streams)
    has_audio = any(stream.is_audio() for stream in metadata.streams)
    return has_video, has_audio


def media_metadata(source: Path) -> dict:
    """Duration and bit rate, or ``{}`` when the file cannot be probed."""
    try:
        import ffmpeg

        probe = ffmpeg.probe(str(source))
    except Exception:
        return {}

    container = probe.get("format") or {}
    metadata: dict = {}

    duration = container.get("duration")
    if duration is not None:
        try:
            metadata["duration_ms"] = int(float(duration) * 1000)
        except (TypeError, ValueError):
            pass

    bit_rate = container.get("bit_rate")
    if bit_rate is not None:
        try:
            metadata["bit_rate"] = int(float(bit_rate))
        except (TypeError, ValueError):
            pass

    return metadata


def image(source: Path, output_stem: Path) -> tuple[dict | None, bool]:
    """Flat JPEG derivatives, plus deep-zoom tiles for a large image.

    Returns ``(exif metadata, whether tiles were produced)``.

    THE EXIF IS RETURNED WHOLE and stored whole. It carries GPS coordinates,
    camera serial numbers and owner names — which is why the records API
    summarises and filters `processing` rather than serving it. Storing it is
    the archival decision; not serving it is the access-control one, and they
    are separate on purpose.
    """
    import pyvips

    try:
        metadata = _exif(source)

        if source.suffix.lower() in RAW_EXTENSIONS:
            full = _decode_raw(source)
            for suffix, edge, quality in IMAGE_SIZES:
                full.thumbnail_image(edge).write_to_file(
                    f"{output_stem}{suffix}.jpg", Q=quality, optimize_coding=True
                )
        else:
            for suffix, edge, quality in IMAGE_SIZES:
                thumbnail = pyvips.Image.thumbnail(str(source), edge)
                thumbnail.write_to_file(
                    f"{output_stem}{suffix}.jpg", Q=quality, optimize_coding=True
                )

            # Sequential access: the tiler streams the image rather than holding
            # it, which is what makes a 2GB TIFF possible at all.
            full = pyvips.Image.new_from_file(str(source), access="sequential")

        needs_tiles = max(full.width, full.height) >= DZI_THRESHOLD
        if needs_tiles:
            full.dzsave(f"{output_stem}_tiles")
    except Exception as exc:
        raise ProcessingFailed(f"Could not derive images from {source.name}") from exc

    return metadata, needs_tiles


def _decode_raw(source: Path):
    """A camera RAW file as an upright 8-bit sRGB vips image.

    LibRaw applies the camera's white balance and the orientation recorded in
    the file, so the derivatives face the way the photographer held the camera.
    The decoded image is held in memory; a 60-megapixel frame is under 200MB.
    """
    import pyvips
    import rawpy

    with rawpy.imread(str(source)) as raw:
        pixels = raw.postprocess(use_camera_wb=True, output_bps=8)

    return pyvips.Image.new_from_array(pixels, interpretation="srgb")


def _exif(source: Path) -> dict | None:
    """EXIF for one file, or ``None``. A missing exiftool is not fatal: the
    derivatives are still produced."""
    try:
        import exiftool

        with exiftool.ExifToolHelper() as tool:
            found = tool.get_metadata([str(source)])
        return found[0] if found else None
    except Exception:
        logger.warning("Could not read EXIF from %s", source.name)
        return None


def pdf_pages(source: Path, output_root: Path) -> bool:
    """Page images for a PDF, at the two sizes the viewer requests.

    The directory names are ``web/big`` and ``web/small``, which is the contract
    ``records/viewers.py``'s ``PAGE_DIRECTORIES`` allowlist reads.
    """
    from pdf2image import convert_from_path

    big = output_root / "web" / "big"
    small = output_root / "web" / "small"

    try:
        big.mkdir(parents=True, exist_ok=True)
        small.mkdir(parents=True, exist_ok=True)

        pages = convert_from_path(str(source), output_folder=str(big), fmt="jpg", output_file="page_")

        for page in pages:
            page.thumbnail((100, 100))
            stem = Path(page.filename).stem
            page.save(str(small / f"{stem}.jpg"), "JPEG")
    except Exception as exc:
        raise ProcessingFailed(f"Could not render pages from {source.name}") from exc

    return True


def strip_active_content(path: Path) -> bool:
    """Rewrite a PDF without its JavaScript or launch actions.

    Runs before the pages are rendered, and **in place on the archived master**:
    it modifies the archived file itself, not a copy.

    A failure here is not fatal: a PDF that cannot be rewritten still gets its
    page images.
    """
    try:
        import pypdf

        reader = pypdf.PdfReader(str(path))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        root = reader.trailer.get("/Root", {})
        names = root.get("/Names")
        if names is not None and "/JavaScript" in names:
            del names["/JavaScript"]

        action = root.get("/OpenAction")
        if isinstance(action, dict) and action.get("/S") == "/JavaScript":
            del root["/OpenAction"]

        # Written beside the master and moved into place: writing straight
        # over the master means a crash mid-write destroys the archived file.
        scratch = path.with_suffix(path.suffix + ".partial")
        with open(scratch, "wb") as handle:
            writer.write(handle)
        os.replace(scratch, path)
        return True
    except Exception:
        logger.warning("Could not strip active content from %s; continuing", path.name)
        return False


def convert_to_pdf(source, destination) -> None:
    """Convert a document to PDF with LibreOffice, into ``destination``.

    The PDF is written to ``destination``, whatever directory the source is in.
    """
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "libreoffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(source.parent),
            str(source),
        ],
        capture_output=True,
        timeout=LIBREOFFICE_TIMEOUT,
        check=False,
    )

    produced = source.with_suffix(".pdf")
    if result.returncode != 0 or not produced.is_file():
        # stderr goes to the log, never to the caller: LibreOffice prints
        # absolute paths and profile directories.
        logger.error(
            "LibreOffice failed on %s (exit %s): %s",
            source.name,
            result.returncode,
            result.stderr.decode("utf-8", "replace")[:500],
        )
        raise ProcessingFailed(f"Could not convert {source.name} to PDF")

    if produced != destination:
        os.replace(produced, destination)


def document(source: Path, scratch_stem: Path, output_root: Path) -> bool:
    """A Word or text document: convert to PDF, then render its pages."""
    pdf = Path(f"{scratch_stem}.pdf")
    convert_to_pdf(source, pdf)
    return pdf_pages(pdf, output_root)


def tabular(source: Path, output_stem: Path, *, spreadsheet: bool) -> bool:
    """A CSV or spreadsheet: a full CSV copy plus a short preview.

    ``pandas`` is imported inside the function because it is the single heaviest
    import in the dependency set and most processing runs never touch it.
    """
    import shutil

    import pandas as pd

    try:
        if spreadsheet:
            frame = pd.read_excel(source)
            frame.to_csv(f"{output_stem}.csv", index=False, header=True)
        else:
            frame = pd.read_csv(source)
            shutil.copy(source, f"{output_stem}.csv")

        frame.head(PREVIEW_ROWS).to_csv(f"{output_stem}_min.csv", index=False, header=True)
    except Exception as exc:
        raise ProcessingFailed(f"Could not derive a preview from {source.name}") from exc

    return True
