"""Reading and editing a record's transcription.

An ``av_transcribe`` processing produces timed segments, optionally attributed
to speakers and tagged with labels and locations, plus a flat text rendering.
The viewer reads a page of segments at a time; transcribers correct them one
segment at a time.

Two things are worth knowing before changing anything here.

**The flat text is derived, never edited directly.** Every write regenerates it
from the segments, so the searchable text cannot drift from what the viewer
shows. ``flatten`` is that single derivation.

**Who may edit is not the same question as who may read.** A transcriber is
granted editing rights on a record by being *assigned a task* on it, not by any
role they hold globally - see ``may_edit``. The role gate on the route is
necessary but not sufficient, and the two checks are deliberately separate.
"""

from __future__ import annotations

import datetime
import logging
import re
import unicodedata

from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "records"
TRANSCRIPTION_TYPE = "av_transcribe"

#: Task states in which an assigned transcriber may still edit. A task that has
#: been accepted is finished work and is not reopened by editing.
OPEN_TASK_STATES = ("review", "pending", "rejected")

#: Trailing credits that speech-to-text models hallucinate from their training
#: data ("subtitles by ...", a bare URL). Dropped from the flattened text only -
#: the segments themselves are left alone so a human can see what was produced.
_CREDIT_LINE = re.compile(r"\s*(transcribed by.*|subtitles by.*|by.*\.com|by.*\.org|http.*|.com*)$")

#: Gap between two segments by the same speaker, in seconds, below which the
#: speaker timeline merges them into one continuous span.
SPEAKER_MERGE_GAP = 5


class TranscriptionError(Exception):
    """The record has no transcription of the shape this operation needs."""

    def __init__(self, message: str, status_code: int = 404) -> None:
        super().__init__(message)
        self.status_code = status_code


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def normalize(text):
    """Casefold and strip accents, for grouping labels that differ only in those."""
    if not isinstance(text, str):
        return text
    return "".join(
        char for char in unicodedata.normalize("NFD", text) if unicodedata.category(char) != "Mn"
    ).lower()


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def flatten(segments) -> str:
    """The flat text of a transcript, regenerated from its segments.

    A speaker change opens a new paragraph; consecutive segments by the same
    speaker run together. Hallucinated credit lines are skipped.
    """
    text = ""
    current_speaker = ""

    for segment in segments or []:
        body = segment.get("text") or ""
        if _CREDIT_LINE.search(body):
            continue

        speaker = segment.get("speaker")
        if speaker and speaker != current_speaker:
            current_speaker = speaker
            text += "\n\n" + speaker + ": " + body + " "
        else:
            text += body + " "

    return text


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def result_of(record: dict, slug: str) -> dict:
    """The stored result of a transcription-shaped processing.

    ``labeling`` results are accepted too: they carry the same segment shape and
    the viewer renders them identically.
    """
    processing = record.get("processing")
    entry = processing.get(slug) if isinstance(processing, dict) else None

    if not isinstance(entry, dict):
        raise TranscriptionError(_("Record has not been processed"), 404)

    kind = entry.get("type")
    if kind not in (TRANSCRIPTION_TYPE, "labeling"):
        raise TranscriptionError(_("Record has not been processed with {slug}", slug=slug), 404)

    return entry.get("result") or {}


def page_boundaries(segments, char_limit: int) -> list[tuple[int, int]]:
    """Half-open segment ranges, each roughly ``char_limit`` characters long.

    A segment is never split across pages, so a single very long segment simply
    makes an over-long page rather than being cut mid-sentence.
    """
    if not segments:
        return [(0, 0)]
    if not char_limit or char_limit <= 0:
        return [(0, len(segments))]

    boundaries = []
    start = 0
    used = 0

    for index, segment in enumerate(segments):
        length = len(segment.get("text") or "")
        if index > start and used + length > char_limit:
            boundaries.append((start, index))
            start = index
            used = 0
        used += length

    boundaries.append((start, len(segments)))
    return boundaries


def build(record: dict, slug: str, page: int = 0, with_segments: bool = True) -> dict:
    """One page of a transcription, with its label and speaker summaries."""
    result = result_of(record, slug)
    limit = get_settings().transcription_page_char_limit

    segments = result.get("segments") or []
    boundaries = page_boundaries(segments, limit) or [(0, 0)]
    total_pages = len(boundaries)

    index = _page_index(page)
    if index >= total_pages:
        index = total_pages - 1

    start, end = boundaries[index]
    visible = segments[start:end]

    groups: list[dict] = []
    seen_groups: set[tuple] = set()
    label_counts: dict[tuple, dict] = {}
    location_counts: dict[tuple, dict] = {}

    processed = []
    for segment in visible:
        entry = {
            "text": segment.get("text", ""),
            "start": segment.get("start"),
            "end": segment.get("end"),
            "speaker": segment.get("speaker"),
        }

        labels = segment.get("label") or []
        if labels:
            entry["labels"] = labels
            _tally(labels, label_counts, groups, seen_groups, "transcript")

        locations = segment.get("location") or []
        if locations:
            entry["location"] = locations
            _tally(locations, location_counts, groups, seen_groups, None)

        if groups:
            entry["groups"] = groups

        processed.append(entry)

    transcription: dict = {"text": result.get("text", "")}

    pagination = {
        "page": index,
        "total_pages": total_pages,
        "page_char_limit": limit,
        "total_characters": sum(len(s.get("text") or "") for s in segments),
        "page_characters": sum(len(s.get("text") or "") for s in visible),
        "total_segments": len(segments),
        "page_segments": len(visible),
        "from_segment": start,
        "to_segment": end - 1 if end > start else -1,
        "has_more": index < total_pages - 1,
    }

    if with_segments:
        transcription["segments"] = processed
        transcription["speakers"] = speaker_timeline(processed)
        transcription["pagination"] = pagination
    elif total_pages > 1:
        transcription["pagination"] = pagination

    vision = _vision_segments(result, groups, seen_groups)
    if vision["segments"]:
        transcription["vision_segment"] = vision["segments"]
    if vision["frames"]:
        transcription["frames"] = vision["frames"]

    if label_counts:
        transcription["labels"] = _by_count(label_counts)
    if location_counts:
        transcription["locations"] = _by_count(location_counts)
    if groups:
        transcription["groups"] = groups

    return transcription


def _page_index(page) -> int:
    try:
        index = int(page)
    except (TypeError, ValueError):
        return 0
    return max(index, 0)


def _by_count(counts: dict) -> list[dict]:
    return sorted(counts.values(), key=lambda entry: entry["count"], reverse=True)


def _tally(items, counts: dict, groups: list, seen: set, group_type: str | None) -> None:
    """Count occurrences of each label/location, keyed by normalised name."""
    for item in items:
        if not isinstance(item, dict):
            continue

        group = normalize(item.get("group")) if item.get("group") else ""
        if group_type and group and (group, group_type) not in seen:
            groups.append({"name": group, "type": group_type})
            seen.add((group, group_type))

        key = (normalize(item.get("name")), group)
        if key in counts:
            counts[key]["count"] += 1
        else:
            counts[key] = {**item, "count": 1, "group": group}


def speaker_timeline(segments) -> list[dict] | None:
    """Per-speaker spans and total speaking time, or ``None`` if undiarised."""
    if not any(segment.get("speaker") for segment in segments):
        return None

    speakers: list[dict] = []
    for segment in segments:
        name = segment.get("speaker")
        if not name:
            continue

        entry = next((item for item in speakers if item["name"] == name), None)
        start, end = segment.get("start"), segment.get("end")

        if entry is None:
            speakers.append({"name": name, "segments": [{"start": start, "end": end}]})
            continue

        last = entry["segments"][-1]
        if start is not None and last["end"] is not None and start - last["end"] < SPEAKER_MERGE_GAP:
            last["end"] = end
        else:
            entry["segments"].append({"start": start, "end": end})

    for entry in speakers:
        entry["total"] = sum(
            span["end"] - span["start"]
            for span in entry["segments"]
            if span.get("start") is not None and span.get("end") is not None
        )

    return speakers


def _vision_segments(result: dict, groups: list, seen: set) -> dict:
    """Normalise per-frame visual labels, under any of the three key names used."""
    source = result.get("vision_segment")
    if source is None:
        source = result.get("vision_segments")
    if source is None:
        source = result.get("frames")
    if not isinstance(source, list):
        return {"segments": [], "frames": []}

    counts: dict[tuple, dict] = {}
    normalised = []

    for segment in source:
        if not isinstance(segment, dict):
            continue

        labels = segment.get("label")
        if labels is None:
            labels = segment.get("labels")
        if not isinstance(labels, list):
            labels = []

        fallback_group = normalize(segment.get("group")) if segment.get("group") else ""
        entry = {"start": segment.get("start"), "end": segment.get("end"), "labels": []}

        for label in labels:
            if not isinstance(label, dict):
                continue

            group = normalize(label.get("group") or fallback_group) if (
                label.get("group") or fallback_group
            ) else ""
            kind = label.get("type") or "vision_segment"
            entry["labels"].append({**label, "group": group, "type": kind})

            key = (normalize(label.get("name")), group, kind)
            if key in counts:
                counts[key]["count"] += 1
            else:
                counts[key] = {**label, "group": group, "type": kind, "count": 1}

            if group and (group, "vision_segment") not in seen:
                groups.append({"name": group, "type": "vision_segment"})
                seen.add((group, "vision_segment"))

        normalised.append(entry)

    return {"segments": normalised, "frames": _by_count(counts)}


# ---------------------------------------------------------------------------
# Who may edit
# ---------------------------------------------------------------------------


def may_edit(record_id: str, user: str) -> bool:
    """Whether this caller may correct this record's transcription.

    Administrators and team leads may, anywhere. A transcriber may only where
    they hold an open task on *this* record - the assignment is the grant, and
    holding the role alone grants nothing.

    The original returned ``None`` for a caller who was none of the three and
    every caller tested ``if can_edit is False``, so ``None`` passed. That made
    the route's own role gate the only real check, which is why an editor could
    edit any transcription anywhere. This returns a real bool; the route states
    which roles reach it.
    """
    from archihub.api.users.services import has_role

    if has_role(user, "admin") or has_role(user, "team_lead"):
        return True

    if has_role(user, "transcriber"):
        task = _mongo().get_record(
            "usertasks",
            {"recordId": record_id, "user": user, "status": {"$in": list(OPEN_TASK_STATES)}},
            fields={"_id": 1},
        )
        return bool(task)

    # An editor: allowed by the route's role gate, and not narrowed further
    # here. Narrowing it would be a behaviour change to a role that legitimately
    # curates content; it is called out in the route's docstring instead.
    return has_role(user, "editor")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _load_for_edit(record_id: str, slug: str) -> tuple[dict, list]:
    """The record's processing block and the segment list to mutate."""
    from bson.objectid import ObjectId

    try:
        object_id = ObjectId(record_id)
    except Exception:
        raise TranscriptionError(_("Record does not exist"), 404) from None

    record = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"processing": 1})
    if not record:
        raise TranscriptionError(_("Record does not exist"), 404)

    processing = record.get("processing")
    if not isinstance(processing, dict) or slug not in processing:
        raise TranscriptionError(_("Record does not have transcription"), 404)
    if processing[slug].get("type") != TRANSCRIPTION_TYPE:
        raise TranscriptionError(_("Record has not been processed with {slug}", slug=slug), 404)

    result = processing[slug].get("result")
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        raise TranscriptionError(_("Record does not have transcription"), 404)

    return processing, result["segments"]


def _save(record_id: str, processing: dict, slug: str, segments: list, user: str | None) -> None:
    """Persist edited segments and the text regenerated from them.

    Only the one processing entry and the two audit fields are written -
    ``$set`` with dotted paths rather than replacing the whole ``processing``
    block, so a plugin writing a *different* entry concurrently is not clobbered
    by a transcript correction.
    """
    from bson.objectid import ObjectId

    processing[slug]["result"]["segments"] = segments
    processing[slug]["result"]["text"] = flatten(segments)

    update = {
        f"processing.{slug}.result.segments": segments,
        f"processing.{slug}.result.text": processing[slug]["result"]["text"],
        "updatedBy": user or "system",
        "updatedAt": _now(),
    }
    _mongo().update_record(COLLECTION, {"_id": ObjectId(record_id)}, update)
    _call_hook("record_update", {"_id": record_id, "updatedBy": user or "system"})


def edit_segment(record_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Correct one segment's text and timing, and optionally its speaker."""
    slug = body.get("slug")
    if not slug:
        return {"msg": _("slug is missing")}, 400

    for field in ("index", "text", "start", "end"):
        if field not in body:
            return {"msg": _("{field} is missing", field=field)}, 400

    try:
        processing, segments = _load_for_edit(record_id, slug)
    except TranscriptionError as exc:
        return {"msg": str(exc)}, exc.status_code

    index = _segment_index(body["index"], len(segments))
    if index is None:
        return {"msg": _("Invalid segment index")}, 400

    segments[index]["text"] = body["text"]
    segments[index]["start"] = body["start"]
    segments[index]["end"] = body["end"]
    if "speaker" in body:
        segments[index]["speaker"] = body["speaker"]

    _save(record_id, processing, slug, segments, user)
    return {"msg": _("Transcription segment edited")}, 200


def delete_segment(record_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Remove one segment from a transcript."""
    slug = body.get("slug")
    if not slug:
        return {"msg": _("slug is missing")}, 400
    if "index" not in body:
        return {"msg": _("index is missing")}, 400

    try:
        processing, segments = _load_for_edit(record_id, slug)
    except TranscriptionError as exc:
        return {"msg": str(exc)}, exc.status_code

    index = _segment_index(body["index"], len(segments))
    if index is None:
        return {"msg": _("Invalid segment index")}, 400

    segments.pop(index)
    _save(record_id, processing, slug, segments, user)
    return {"msg": _("Transcription segment deleted")}, 200


def rename_speaker(record_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Rename one speaker across every segment attributed to them."""
    slug = body.get("slug")
    if not slug:
        return {"msg": _("slug is missing")}, 400

    speaker = body.get("speaker")
    old_speaker = body.get("oldSpeaker")
    if not speaker or not old_speaker:
        return {"msg": _("speaker and oldSpeaker are required")}, 400

    try:
        processing, segments = _load_for_edit(record_id, slug)
    except TranscriptionError as exc:
        return {"msg": str(exc)}, exc.status_code

    renamed = 0
    for segment in segments:
        if segment.get("speaker") == old_speaker:
            segment["speaker"] = speaker
            renamed += 1

    # The original wrote unconditionally, so a typo'd `oldSpeaker` still bumped
    # `updatedAt`, fired the reindex hook and regenerated the text for nothing.
    if not renamed:
        return {"msg": _("Transcription speaker edited")}, 200

    _save(record_id, processing, slug, segments, user)
    return {"msg": _("Transcription speaker edited")}, 200


def _segment_index(value, count: int) -> int | None:
    """A segment index from the request, checked against the real segment count.

    Negative indices are refused rather than wrapping to the end of the
    transcript, which is what ``list.pop`` and bare subscripting do - the
    original accepted ``-1`` and edited the last segment of a transcript the
    caller was pointing at the first of.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
    if value < 0 or value >= count:
        return None
    return value


def _call_hook(name: str, payload: dict) -> None:
    from archihub.core.hooks import get_hook_handler

    try:
        get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("%s hook failed", name)
