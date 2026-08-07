"""Reading and editing a record's transcription.

Two properties are load-bearing and each has its own section below: the flat
text is always regenerated from the segments (so search cannot drift from what
the viewer shows), and a transcriber's editing right comes from an assigned
task, not from the role.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.records import transcription

RECORD_ID = "6a70b833497d4440325c94b1"


class FakeMongo:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.tasks: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        if collection == "usertasks":
            wanted = filters or {}
            states = wanted.get("status", {}).get("$in", [])
            for task in self.tasks:
                if (
                    task.get("recordId") == wanted.get("recordId")
                    and task.get("user") == wanted.get("user")
                    and task.get("status") in states
                ):
                    return task
            return None
        return self.records.get(str((filters or {}).get("_id")))

    def update_record(self, collection, filters, update):
        self.updates.append((filters, update))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(transcription, "_mongo", lambda: fake)
    monkeypatch.setattr(transcription, "_call_hook", lambda *a, **k: None)
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


def roles(monkeypatch, *held):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r in held)


def segments(*texts, speaker=None):
    return [
        {"text": text, "start": index * 2.0, "end": index * 2.0 + 1.5, "speaker": speaker}
        for index, text in enumerate(texts)
    ]


def record(result=None, kind="av_transcribe", slug="whisper"):
    return {
        "_id": ObjectId(RECORD_ID),
        "processing": {
            slug: {
                "type": kind,
                "result": result if result is not None else {"segments": [], "text": ""},
            }
        },
    }


# ---------------------------------------------------------------------------
# The derived text
# ---------------------------------------------------------------------------


def test_text_runs_segments_together():
    assert transcription.flatten(segments("one", "two")) == "one two "


def test_a_speaker_change_opens_a_paragraph():
    parts = segments("hello", speaker="A") + segments("goodbye", speaker="B")

    assert transcription.flatten(parts) == "\n\nA: hello \n\nB: goodbye "


def test_consecutive_segments_by_one_speaker_run_together():
    parts = segments("hello", speaker="A")
    parts += [{"text": "again", "speaker": "A"}]

    assert transcription.flatten(parts) == "\n\nA: hello again "


@pytest.mark.parametrize(
    "hallucination",
    ["subtitles by the Amara community", "transcribed by someone", "http://spam.example"],
)
def test_model_hallucinated_credits_are_left_out_of_the_text(hallucination):
    """Speech models emit these from their training data; they are not speech."""
    parts = [{"text": "real content"}, {"text": hallucination}]

    assert transcription.flatten(parts) == "real content "


def test_the_credit_filter_is_case_sensitive_and_so_misses_most_real_ones():
    """Carried over deliberately, not fixed. Documented as BACKEND_FINDINGS F29.

    The pattern is applied without ``re.IGNORECASE``, so the capitalised form
    these models actually emit is not matched and the credit stays in the text.
    Making it case-insensitive would delete text from stored transcripts on the
    next edit of every affected record - a content change, not a port, and one
    that belongs in its own decision rather than as a side effect of this one.
    """
    parts = [{"text": "real content"}, {"text": "Subtitles by the Amara community"}]

    assert transcription.flatten(parts) == "real content Subtitles by the Amara community "


def test_flattening_nothing_is_the_empty_string():
    assert transcription.flatten([]) == ""
    assert transcription.flatten(None) == ""


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_a_short_transcript_is_one_page():
    assert transcription.page_boundaries(segments("a", "b"), 6000) == [(0, 2)]


def test_pages_break_before_the_character_limit_not_after():
    """A page closes when the *next* segment would exceed the limit.

    So pages hold up to the limit and never more, at the cost of running short:
    five 40-character segments under a 100-character limit make three pages of
    80, 80 and 40 rather than two of 100 and 100.
    """
    parts = [{"text": "x" * 40} for _ in range(5)]

    assert transcription.page_boundaries(parts, 100) == [(0, 2), (2, 4), (4, 5)]


def test_a_segment_longer_than_the_limit_is_never_split():
    parts = [{"text": "x" * 500}, {"text": "y"}]

    assert transcription.page_boundaries(parts, 100) == [(0, 1), (1, 2)]


def test_no_segments_is_still_one_empty_page():
    assert transcription.page_boundaries([], 6000) == [(0, 0)]


def test_build_reports_where_in_the_transcript_the_page_sits(mongo, monkeypatch):
    monkeypatch.setattr(
        transcription, "get_settings", lambda: type("S", (), {"transcription_page_char_limit": 4})()
    )
    doc = record({"segments": segments("aaaa", "bbbb", "cccc"), "text": "aaaa bbbb cccc "})

    page = transcription.build(doc, "whisper", page=1)

    assert page["pagination"]["page"] == 1
    assert page["pagination"]["total_pages"] == 3
    assert page["pagination"]["has_more"] is True
    assert [s["text"] for s in page["segments"]] == ["bbbb"]


def test_a_page_past_the_end_clamps_to_the_last(mongo):
    doc = record({"segments": segments("only"), "text": "only "})

    assert transcription.build(doc, "whisper", page=99)["pagination"]["page"] == 0


def test_a_negative_page_clamps_to_the_first(mongo):
    doc = record({"segments": segments("only"), "text": "only "})

    assert transcription.build(doc, "whisper", page=-5)["pagination"]["page"] == 0


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def test_speakers_get_a_timeline_with_total_speaking_time(mongo):
    parts = [
        {"text": "a", "start": 0.0, "end": 2.0, "speaker": "A"},
        {"text": "b", "start": 3.0, "end": 5.0, "speaker": "A"},
    ]

    speakers = transcription.build(record({"segments": parts}), "whisper")["speakers"]

    # The two spans are less than the merge gap apart, so they are one span.
    assert speakers == [{"name": "A", "segments": [{"start": 0.0, "end": 5.0}], "total": 5.0}]


def test_a_long_gap_starts_a_new_span_for_the_same_speaker(mongo):
    parts = [
        {"text": "a", "start": 0.0, "end": 2.0, "speaker": "A"},
        {"text": "b", "start": 60.0, "end": 62.0, "speaker": "A"},
    ]

    speakers = transcription.build(record({"segments": parts}), "whisper")["speakers"]

    assert speakers[0]["segments"] == [{"start": 0.0, "end": 2.0}, {"start": 60.0, "end": 62.0}]
    assert speakers[0]["total"] == 4.0


def test_an_undiarised_transcript_has_no_speaker_timeline(mongo):
    assert transcription.build(record({"segments": segments("a")}), "whisper")["speakers"] is None


def test_labels_are_counted_and_ranked(mongo):
    parts = [
        {"text": "a", "label": [{"name": "Bogotá", "group": "Cities"}]},
        {"text": "b", "label": [{"name": "bogota", "group": "cities"}]},
        {"text": "c", "label": [{"name": "Cali", "group": "Cities"}]},
    ]

    labels = transcription.build(record({"segments": parts}), "whisper")["labels"]

    # Accents and case are normalised away, so the two spellings are one label.
    assert labels[0]["count"] == 2
    assert labels[1]["count"] == 1


def test_an_unprocessed_slug_is_a_404(mongo):
    with pytest.raises(transcription.TranscriptionError) as exc:
        transcription.build(record(), "nosuch")

    assert exc.value.status_code == 404


def test_a_processing_of_another_kind_is_a_404(mongo):
    with pytest.raises(transcription.TranscriptionError) as exc:
        transcription.build(record(kind="ocr"), "whisper")

    assert exc.value.status_code == 404


def test_a_labeling_result_is_read_as_a_transcript(mongo):
    """It carries the same segment shape and the viewer renders it identically."""
    doc = record({"segments": segments("a"), "text": "a "}, kind="labeling")

    assert transcription.build(doc, "whisper")["text"] == "a "


# ---------------------------------------------------------------------------
# Who may edit
# ---------------------------------------------------------------------------


def test_an_administrator_may_edit_any_transcription(mongo, monkeypatch):
    roles(monkeypatch, "admin")

    assert transcription.may_edit(RECORD_ID, "root") is True


def test_a_team_lead_may_edit_any_transcription(mongo, monkeypatch):
    roles(monkeypatch, "team_lead")

    assert transcription.may_edit(RECORD_ID, "lead") is True


def test_a_transcriber_with_an_open_task_may_edit(mongo, monkeypatch):
    roles(monkeypatch, "transcriber")
    mongo.tasks = [{"recordId": RECORD_ID, "user": "tina", "status": "pending"}]

    assert transcription.may_edit(RECORD_ID, "tina") is True


def test_a_transcriber_without_a_task_may_not(mongo, monkeypatch):
    """The assignment is the grant; holding the role alone grants nothing."""
    roles(monkeypatch, "transcriber")

    assert transcription.may_edit(RECORD_ID, "tina") is False


def test_a_transcriber_whose_task_is_accepted_may_not(mongo, monkeypatch):
    """Accepted work is finished, and is not reopened by editing."""
    roles(monkeypatch, "transcriber")
    mongo.tasks = [{"recordId": RECORD_ID, "user": "tina", "status": "accepted"}]

    assert transcription.may_edit(RECORD_ID, "tina") is False


def test_a_transcribers_task_on_another_record_does_not_carry_over(mongo, monkeypatch):
    roles(monkeypatch, "transcriber")
    mongo.tasks = [{"recordId": "another", "user": "tina", "status": "pending"}]

    assert transcription.may_edit(RECORD_ID, "tina") is False


def test_a_caller_with_none_of_the_roles_is_refused(mongo, monkeypatch):
    """The original returned None here and every caller tested `is False`."""
    result = transcription.may_edit(RECORD_ID, "nobody")

    assert result is False


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


@pytest.fixture
def transcript(mongo):
    mongo.records[RECORD_ID] = record(
        {"segments": segments("first", "second", "third"), "text": "first second third "}
    )
    return mongo


def test_editing_a_segment_regenerates_the_text(transcript):
    payload, status = transcription.edit_segment(
        RECORD_ID,
        {"slug": "whisper", "index": 1, "text": "corrected", "start": 2.0, "end": 3.0},
        "tina",
    )

    assert status == 200
    _filters, update = transcript.updates[0]
    assert update["processing.whisper.result.text"] == "first corrected third "
    assert update["processing.whisper.result.segments"][1]["text"] == "corrected"


def test_editing_writes_only_the_one_processing_entry(transcript):
    """A dotted $set, so a plugin writing a different entry is not clobbered."""
    transcription.edit_segment(
        RECORD_ID,
        {"slug": "whisper", "index": 0, "text": "x", "start": 0.0, "end": 1.0},
        "tina",
    )

    _filters, update = transcript.updates[0]
    assert set(update) == {
        "processing.whisper.result.segments",
        "processing.whisper.result.text",
        "updatedBy",
        "updatedAt",
    }


def test_editing_may_set_a_speaker(transcript):
    transcription.edit_segment(
        RECORD_ID,
        {"slug": "whisper", "index": 0, "text": "x", "start": 0.0, "end": 1.0, "speaker": "A"},
        "tina",
    )

    _filters, update = transcript.updates[0]
    assert update["processing.whisper.result.segments"][0]["speaker"] == "A"


def test_deleting_a_segment_removes_it_and_regenerates_the_text(transcript):
    payload, status = transcription.delete_segment(
        RECORD_ID, {"slug": "whisper", "index": 1}, "tina"
    )

    assert status == 200
    _filters, update = transcript.updates[0]
    assert [s["text"] for s in update["processing.whisper.result.segments"]] == ["first", "third"]
    assert update["processing.whisper.result.text"] == "first third "


@pytest.mark.parametrize("index", [-1, 3, 99, "one", None])
def test_a_segment_index_outside_the_transcript_is_refused(transcript, index):
    """`list.pop(-1)` would delete the last segment of a transcript instead."""
    payload, status = transcription.delete_segment(
        RECORD_ID, {"slug": "whisper", "index": index}, "tina"
    )

    assert status == 400
    assert transcript.updates == []


def test_renaming_a_speaker_touches_every_matching_segment(mongo):
    mongo.records[RECORD_ID] = record(
        {
            "segments": [
                {"text": "a", "speaker": "SPEAKER_00"},
                {"text": "b", "speaker": "SPEAKER_01"},
                {"text": "c", "speaker": "SPEAKER_00"},
            ]
        }
    )

    payload, status = transcription.rename_speaker(
        RECORD_ID, {"slug": "whisper", "speaker": "Ana", "oldSpeaker": "SPEAKER_00"}, "tina"
    )

    assert status == 200
    _filters, update = mongo.updates[0]
    written = [s["speaker"] for s in update["processing.whisper.result.segments"]]
    assert written == ["Ana", "SPEAKER_01", "Ana"]


def test_renaming_a_speaker_who_does_not_appear_writes_nothing(transcript):
    """The original wrote regardless, bumping updatedAt and reindexing for nothing."""
    payload, status = transcription.rename_speaker(
        RECORD_ID, {"slug": "whisper", "speaker": "Ana", "oldSpeaker": "nobody"}, "tina"
    )

    assert status == 200
    assert transcript.updates == []


def test_renaming_without_both_names_is_refused(transcript):
    payload, status = transcription.rename_speaker(RECORD_ID, {"slug": "whisper"}, "tina")

    assert status == 400
    assert transcript.updates == []


def test_editing_without_a_slug_is_refused(transcript):
    payload, status = transcription.edit_segment(RECORD_ID, {"index": 0}, "tina")

    assert status == 400


def test_editing_a_missing_field_is_refused(transcript):
    payload, status = transcription.edit_segment(
        RECORD_ID, {"slug": "whisper", "index": 0, "text": "x"}, "tina"
    )

    assert status == 400
    assert transcript.updates == []


def test_editing_a_record_that_does_not_exist_is_a_404(mongo):
    payload, status = transcription.edit_segment(
        RECORD_ID, {"slug": "whisper", "index": 0, "text": "x", "start": 0, "end": 1}, "tina"
    )

    assert status == 404


def test_editing_a_processing_of_another_kind_is_a_404(mongo):
    mongo.records[RECORD_ID] = record({"segments": segments("a")}, kind="ocr")

    payload, status = transcription.edit_segment(
        RECORD_ID, {"slug": "whisper", "index": 0, "text": "x", "start": 0, "end": 1}, "tina"
    )

    assert status == 404
