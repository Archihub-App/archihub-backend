"""transcribeWhisperX — the parts of the task body that can be tested.

Nothing here loads a model or decodes audio. Transcription itself is a Celery
task over GPU libraries, so the strategy is the one Phases 4 and 5 settled on:
pull the decisions out of the loop as pure functions and test those, because a
background job is the easiest place in this codebase for a defect to live for
years — it reports success, and the only symptom is an absence.

The three that matter most:

* **The processing key.** It is where finished transcripts live and how the
  bulk filter decides a record still needs doing. Getting it wrong orphans
  every existing transcript *and* silently re-runs hours of GPU work.
* **The overwrite guard.** Without it, every run re-transcribes everything.
* **Speaker attribution.** Whisper's segments and pyannote's turns are
  independent, so a segment must go to whoever talks over most of it, not to
  whoever happens to start first.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

#: SKIPPED WHEN THE PLUGIN IS NOT INSTALLED, and that is the normal case for a
#: fresh clone. `archihub/plugins/*` is gitignored apart from the five that ship
#: with the backend, so this file is committed while the package it tests is
#: not - importing it at module scope would turn every checkout without the
#: plugin into a collection error, which reads as a broken test suite rather
#: than as an absent optional component.
plugin = pytest.importorskip(
    "archihub.plugins.transcribeWhisperX",
    reason="transcribeWhisperX is not installed in this checkout",
)


# ---------------------------------------------------------------------------
# The processing key is a data contract
# ---------------------------------------------------------------------------


def test_the_processing_key_matches_the_package_directory():
    """Route prefix, dotted task names, storage key and directory are one name.

    Records already carry transcripts under this key, and
    `records/transcription.py` resolves a transcript by the slug the client
    sends. A rename here is unreachable data, not a cosmetic change.
    """
    assert plugin.SLUG == "transcribeWhisperX"
    assert plugin.PROCESSING_KEY == "transcribeWhisperX"
    assert plugin.TASK_BULK == "transcribeWhisperX.bulk"
    assert plugin.TASK_DOWNLOAD == "transcribeWhisperX.download"


def test_the_stored_entry_declares_the_type_the_viewer_requires():
    """`transcription.result_of` refuses an entry whose type is anything else."""
    from archihub.api.records import transcription

    assert plugin.TRANSCRIPTION_TYPE == transcription.TRANSCRIPTION_TYPE


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, resources=()):
        self.resources = list(resources)
        self.queries: list[tuple] = []

    def get_all_records(self, collection, filters=None, fields=None):
        self.queries.append((collection, filters))
        return list(self.resources)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake


def test_an_explicit_record_selection_is_used_as_is(mongo):
    ident = str(ObjectId())

    filters = plugin.record_filters({"records": [ident]})

    assert filters == {"_id": {"$in": [ObjectId(ident)]}}
    assert mongo.queries == []


def test_a_selection_without_a_content_type_is_refused(mongo):
    with pytest.raises(ValueError):
        plugin.record_filters({})


def test_a_content_type_selection_covers_only_media_records(mongo):
    """Transcribing a PDF is not a thing. The selection is narrowed to what
    `fileProcessing` labelled audio or video, so a mixed archive does not queue
    work that can only fail."""
    mongo.resources = [{"_id": ObjectId()}]

    filters = plugin.record_filters({"post_type": "interview"})

    assert filters["processing.fileProcessing.type"] == {"$in": ["audio", "video"]}
    assert filters["processing.fileProcessing"] == {"$exists": True}


def test_a_malformed_identifier_is_refused_rather_than_raising_bson(mongo):
    with pytest.raises(ValueError):
        plugin.record_filters({"records": ["not-an-object-id"]})


# ---------------------------------------------------------------------------
# The overwrite guard
# ---------------------------------------------------------------------------


def test_without_overwrite_already_transcribed_records_are_skipped():
    """The only thing between a re-run and repeating every transcription."""
    filters = plugin._apply_overwrite({"post_type": "x"}, overwrite=False)

    assert filters["processing.transcribeWhisperX"] == {"$exists": False}


def test_with_overwrite_the_selection_is_untouched():
    original = {"post_type": "x"}

    assert plugin._apply_overwrite(original, overwrite=True) == original


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


def test_the_ffmpeg_command_is_a_list_and_asks_for_what_whisper_reads():
    command = plugin.extraction_command("/in.mp4", "/out.wav", denoise=False)

    assert isinstance(command, list)
    assert command[0] == "ffmpeg"
    assert "16000" in command and "pcm_s16le" in command
    assert "-ac" in command and command[command.index("-ac") + 1] == "1"


def test_denoising_is_applied_only_when_asked():
    plain = plugin.extraction_command("/in.mp4", "/out.wav", denoise=False)
    filtered = plugin.extraction_command("/in.mp4", "/out.wav", denoise=True)

    assert not any("afftdn" in part for part in plain)
    assert any("afftdn" in part for part in filtered)


def test_the_filter_is_placed_before_the_output_not_appended():
    """`-af` after the output path is not an argument ffmpeg reads."""
    command = plugin.extraction_command("/in.mp4", "/out.wav", denoise=True)

    assert command.index("-af") < command.index("/out.wav")


# ---------------------------------------------------------------------------
# Speaker attribution
# ---------------------------------------------------------------------------


class Turn:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class FakeDiarization:
    def __init__(self, tracks):
        self._tracks = tracks

    def itertracks(self, yield_label=False):
        for start, end, speaker in self._tracks:
            yield Turn(start, end), None, speaker


def test_a_segment_goes_to_whoever_talks_over_most_of_it():
    """Not to whoever starts first. A speaker who says one word at the start of
    someone else's sentence would otherwise take the whole segment."""
    segments = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    diarization = FakeDiarization(
        [(0.0, 1.0, "SPEAKER_00"), (1.0, 10.0, "SPEAKER_01")]
    )

    plugin.attribute_speakers(segments, diarization)

    assert segments[0]["speaker"] == "PERSONA_01"


def test_a_segment_nobody_overlaps_is_marked_unknown_not_left_missing():
    """A missing key and an unknown speaker read differently downstream."""
    segments = [{"start": 50.0, "end": 60.0, "text": "hello"}]

    plugin.attribute_speakers(segments, FakeDiarization([(0.0, 1.0, "SPEAKER_00")]))

    assert segments[0]["speaker"] == "PERSONA_UNKNOWN"


# ---------------------------------------------------------------------------
# Assembling the transcript
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Subtitles by the Amara.org community",
        "Transcribed by someone",
        "http://example.com",
    ],
)
def test_whisper_boilerplate_is_dropped_from_the_transcript(text):
    """Whisper emits these over silence. They are not speech and must not be
    stored as if they were - they end up in search and in exports."""
    segments = [{"start": 0, "end": 1, "text": text}]

    assert plugin.assemble_text(segments, diarize=False) == ""
    assert segments[0]["text"] == ""


def test_real_speech_is_kept():
    segments = [{"start": 0, "end": 1, "text": "the archive opened in 1975"}]

    assert "1975" in plugin.assemble_text(segments, diarize=False)


def test_a_speaker_is_labelled_once_per_turn_not_once_per_segment():
    segments = [
        {"start": 0, "end": 1, "text": "one", "speaker": "PERSONA_00"},
        {"start": 1, "end": 2, "text": "two", "speaker": "PERSONA_00"},
        {"start": 2, "end": 3, "text": "three", "speaker": "PERSONA_01"},
    ]

    text = plugin.assemble_text(segments, diarize=True)

    assert text.count("PERSONA_00:") == 1
    assert text.count("PERSONA_01:") == 1


def test_only_the_segment_opening_a_turn_is_tagged_for_the_subtitle_export():
    segments = [
        {"start": 0, "end": 1, "text": "one", "speaker": "PERSONA_00"},
        {"start": 1, "end": 2, "text": "two", "speaker": "PERSONA_00"},
    ]

    plugin.assemble_text(segments, diarize=True)

    assert "speaker_tag" in segments[0]
    assert "speaker_tag" not in segments[1]


# ---------------------------------------------------------------------------
# SRT export
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (61.25, "00:01:01,250"),
        (3661.007, "01:01:01,007"),
    ],
)
def test_srt_timestamps_are_the_only_form_the_format_accepts(seconds, expected):
    assert plugin.srt_timestamp(seconds) == expected


def test_a_negative_timestamp_is_clamped_rather_than_written_as_nonsense():
    """`-00:00:01,000` is not parseable, and one bad cue can drop a whole file."""
    assert plugin.srt_timestamp(-5) == "00:00:00,000"


def test_srt_cues_are_numbered_from_one_and_in_order():
    segments = [
        {"start": 0, "end": 1, "text": "one"},
        {"start": 1, "end": 2, "text": "two"},
    ]

    srt = plugin.to_srt(segments)

    assert srt.startswith("1\n")
    assert "\n2\n" in srt
    assert "-->" in srt


def test_a_dropped_hallucination_does_not_become_a_blank_cue():
    """`assemble_text` empties the text but keeps the timings, so the exporter
    has to skip it - some players render a blank cue as a subtitle flash, and
    the numbering would skip a value."""
    segments = [
        {"start": 0, "end": 1, "text": "real speech"},
        {"start": 1, "end": 2, "text": ""},
        {"start": 2, "end": 3, "text": "more speech"},
    ]

    srt = plugin.to_srt(segments)

    assert "1\n" in srt and "2\n" in srt
    assert "3\n" not in srt


def test_the_speaker_tag_is_prepended_to_the_cue_when_there_is_one():
    segments = [{"start": 0, "end": 1, "text": "hello", "speaker_tag": "[PERSONA_00] "}]

    assert "[PERSONA_00] hello" in plugin.to_srt(segments)


# ---------------------------------------------------------------------------
# Export format
# ---------------------------------------------------------------------------


def test_the_export_format_is_an_allowlist():
    """`format` reaches a file extension and a code path."""
    assert set(plugin.FORMATS) == {"doc", "pdf", "srt"}


def test_an_unsupported_format_is_refused_before_anything_is_written():
    with pytest.raises(ValueError):
        plugin.download_task({"format": "../../etc/passwd", "records": []}, "alice")
