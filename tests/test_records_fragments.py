"""Cutting a fragment out of a recording.

This is what an audio or video snap renders, and what
``/records/{id}/stream?start_ms=&end_ms=`` serves. It shells out to ffmpeg, so
the argument list is asserted directly - a command built as a list and never a
shell string is the property worth pinning.
"""

from __future__ import annotations

import subprocess

import pytest

from archihub.api.records import media


@pytest.fixture
def temporal(tmp_path, monkeypatch):
    class Settings:
        temporal_files_path = str(tmp_path / "temporal")
        web_files_path = str(tmp_path / "web")
        original_files_path = str(tmp_path / "original")

    monkeypatch.setattr(media, "get_settings", lambda: Settings())
    return tmp_path


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_command_is_a_list_not_a_shell_string():
    command = media.fragment_command("/in.mp4", "/out.mp4", 1.0, 2.0, "video")

    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert command[0] == "ffmpeg"


def test_seeking_comes_after_the_input_so_it_is_frame_accurate():
    """A snap of a spoken phrase that starts a keyframe early is a wrong snap."""
    command = media.fragment_command("/in.mp4", "/out.mp4", 5.0, 3.0, "video")

    assert command.index("-i") < command.index("-ss")


def test_the_duration_is_the_length_not_the_end_time():
    command = media.fragment_command("/in.mp3", "/out.mp3", 10.0, 4.0, "audio")

    assert command[command.index("-ss") + 1] == "10.000"
    assert command[command.index("-t") + 1] == "4.000"


def test_audio_and_video_get_their_own_codecs():
    video = media.fragment_command("/in.mp4", "/out.mp4", 0.0, 1.0, "video")
    audio = media.fragment_command("/in.mp3", "/out.mp3", 0.0, 1.0, "audio")

    assert "libx264" in video
    assert "libmp3lame" in audio
    assert "faststart" in video


def test_a_filename_with_shell_metacharacters_stays_one_argument():
    """It cannot be a filename today, but the command must not care if it were."""
    command = media.fragment_command("/in; rm -rf /.mp4", "/out.mp4", 0.0, 1.0, "video")

    assert "/in; rm -rf /.mp4" in command


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _fake_run(returncode=0, writes=b"data", stderr=b""):
    def run(command, capture_output=False, timeout=None):
        destination = command[-1]
        if writes is not None:
            with open(destination, "wb") as handle:
                handle.write(writes)
        return subprocess.CompletedProcess(command, returncode, b"", stderr)

    return run


def test_a_successful_extraction_returns_a_file(temporal, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run())

    result = media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")

    assert result.is_file()
    assert result.suffix == ".mp3"


def test_each_extraction_gets_a_fresh_name(temporal, monkeypatch):
    """The original built the name from the record id and the offsets and reused
    whatever it found, so a fragment left by a failed run was served as real."""
    monkeypatch.setattr(subprocess, "run", _fake_run())

    first = media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")
    second = media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")

    assert first != second


def test_a_failing_ffmpeg_leaves_nothing_behind(temporal, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1, writes=b"partial"))

    with pytest.raises(media.FragmentFailed):
        media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")

    assert list((temporal / "temporal").iterdir()) == []


def test_an_empty_output_is_treated_as_failure(temporal, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(writes=b""))

    with pytest.raises(media.FragmentFailed):
        media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")


def test_ffmpeg_stderr_never_reaches_the_caller(temporal, monkeypatch):
    """It names paths on the server. The original returned it as the message."""
    monkeypatch.setattr(
        subprocess, "run", _fake_run(returncode=1, stderr=b"/srv/archihub/webfiles/secret.mp3: bad")
    )

    with pytest.raises(media.FragmentFailed) as exc:
        media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")

    assert "/srv" not in str(exc.value)


def test_a_hung_ffmpeg_is_killed_rather_than_holding_the_worker(temporal, monkeypatch):
    """The original had no timeout at all."""

    def hang(command, capture_output=False, timeout=None):
        raise subprocess.TimeoutExpired(command, timeout or 0)

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(media.FragmentFailed):
        media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")


def test_a_missing_ffmpeg_is_reported_as_a_failure_not_a_crash(temporal, monkeypatch):
    def missing(command, capture_output=False, timeout=None):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(media.FragmentFailed):
        media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")


def test_an_absurdly_long_fragment_is_refused_before_transcoding(temporal, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))

    with pytest.raises(media.FragmentFailed):
        media.extract_fragment("/in.mp3", 0.0, media.MAX_FRAGMENT_SECONDS + 1, "audio")

    assert called == []


def test_an_unconfigured_temporal_path_is_refused(tmp_path, monkeypatch):
    class Settings:
        temporal_files_path = ""

    monkeypatch.setattr(media, "get_settings", lambda: Settings())

    with pytest.raises(media.FragmentFailed):
        media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")


def test_an_image_cannot_have_a_fragment_cut_from_it(temporal):
    with pytest.raises(media.NotStreamable):
        media.extract_fragment("/in.jpg", 1.0, 3.0, "image")


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_no_bounds_asked_for_is_none():
    assert media.parse_fragment_bounds(None, None) is None


def test_a_valid_range_is_returned_as_floats():
    assert media.parse_fragment_bounds("1.5", "4") == (1.5, 4.0)


@pytest.mark.parametrize(
    "pair", [(1.0, None), (None, 2.0), (3.0, 1.0), (-1.0, 2.0), (1.0, 1.0), ("a", "b")]
)
def test_an_unusable_range_raises_rather_than_serving_the_whole_file(pair):
    """Silently serving everything looks to the user like seeking that does nothing."""
    with pytest.raises(ValueError):
        media.parse_fragment_bounds(*pair)


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


def test_stale_fragments_are_swept_on_the_next_extraction(temporal, monkeypatch):
    """A client that disconnects mid-stream leaves its fragment behind."""
    import os
    import time

    directory = temporal / "temporal"
    directory.mkdir(parents=True)
    stale = directory / f"{media.FRAGMENT_PREFIX}old.mp3"
    stale.write_bytes(b"x")
    old = time.time() - media.STALE_FRAGMENT_SECONDS - 60
    os.utime(stale, (old, old))

    monkeypatch.setattr(subprocess, "run", _fake_run())
    media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")

    assert not stale.exists()


def test_a_fresh_fragment_is_not_swept(temporal, monkeypatch):
    directory = temporal / "temporal"
    directory.mkdir(parents=True)
    fresh = directory / f"{media.FRAGMENT_PREFIX}new.mp3"
    fresh.write_bytes(b"x")

    monkeypatch.setattr(subprocess, "run", _fake_run())
    media.extract_fragment("/in.mp3", 1.0, 3.0, "audio")

    assert fresh.exists()


def test_the_sweep_only_touches_files_it_generated(temporal):
    """The temporal directory is shared with plugin scratch files."""
    import os
    import time

    directory = temporal / "temporal"
    directory.mkdir(parents=True)
    someone_elses = directory / "massiveUpdater-batch.csv"
    someone_elses.write_bytes(b"x")
    old = time.time() - media.STALE_FRAGMENT_SECONDS - 60
    os.utime(someone_elses, (old, old))

    assert media.sweep_stale_fragments(directory) == 0
    assert someone_elses.exists()


def test_sweeping_a_directory_that_does_not_exist_is_harmless(tmp_path):
    assert media.sweep_stale_fragments(tmp_path / "nope") == 0
