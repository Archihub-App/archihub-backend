"""Reasoning deltas become named steps, and never become the answer.

Two separate properties, and the second is the one that bites.

Several providers stream the model's private reasoning alongside its reply.
Because it is fluent prose in the same voice, a user cannot tell it apart once
the two are concatenated — the answer simply begins with the model talking to
itself. Google's dialect was doing exactly that: reasoning arrives as an ordinary
text part flagged ``thought: true``, in the same list as the answer, and the text
builder took every part.
"""

from __future__ import annotations

import pytest

from archihub.api.aiservices.thinking import ThinkingSteps


def _titles(events):
    return [(e["status"], e["title"]) for e in events]


# ---------------------------------------------------------------------------
# Recognising steps
# ---------------------------------------------------------------------------


def test_a_heading_line_opens_a_step():
    steps = ThinkingSteps()

    assert _titles(steps.consume("Reading the transcript: looking for the summary\n")) == [
        ("running", "Reading the transcript")
    ]


def test_a_step_closes_when_the_next_one_opens():
    steps = ThinkingSteps()
    steps.consume("First pass: reading\n")

    events = steps.consume("Second pass: summarising\n")

    assert _titles(events) == [("done", "First pass"), ("running", "Second pass")]


def test_a_line_split_across_deltas_is_still_one_step():
    """Reasoning arrives in arbitrary fragments, not in lines."""
    steps = ThinkingSteps()

    assert steps.consume("Reading the tra") == []
    events = steps.consume("nscript: looking for the summary\n")

    assert _titles(events) == [("running", "Reading the transcript")]


def test_the_same_heading_repeated_is_not_a_second_step():
    """A model restating its heading mid-thought is still one step.

    Note the heading must be a real one: "Analysis" is in the generic list, so
    its description becomes the title and two descriptions are two steps. That
    is deliberate, and it is what the next test covers.
    """
    steps = ThinkingSteps()
    steps.consume("Reading the transcript: one\n")

    assert steps.consume("Reading the transcript: two\n") == []


def test_a_generic_heading_borrows_the_description():
    """"Step 2" names nothing; what follows the colon does."""
    steps = ThinkingSteps()

    events = steps.consume("Step 2: identify the speakers in the interview\n")

    assert _titles(events) == [("running", "identify the speakers in the interview")]


def test_a_bullet_is_a_step():
    steps = ThinkingSteps()

    assert _titles(steps.consume("- check the dates\n")) == [("running", "check the dates")]


def test_ordinary_prose_is_not_a_step():
    steps = ThinkingSteps()

    assert steps.consume("I should probably read this carefully first.\n") == []


def test_a_long_unterminated_line_still_opens_a_step():
    """Some providers stream one long line with no trailing newline."""
    steps = ThinkingSteps()

    events = steps.consume("Reviewing the speaker labels: " + "x" * 80)

    assert [e["status"] for e in events] == ["running"]


def test_a_very_long_title_is_truncated():
    steps = ThinkingSteps()

    events = steps.consume("A" * 200 + ": something\n")

    assert len(events[0]["title"]) <= 72


def test_finalize_closes_the_open_step():
    steps = ThinkingSteps()
    steps.consume("Reading: one\n")

    assert _titles(steps.finalize()) == [("done", "Reading")]


def test_finalize_on_an_empty_stream_yields_nothing():
    assert ThinkingSteps().finalize() == []


def test_the_summary_lists_every_step_once():
    steps = ThinkingSteps()
    steps.consume("First: a\nSecond: b\n")
    steps.finalize()

    assert [s["title"] for s in steps.summary()] == ["First", "Second"]
    assert all({"step_id", "order", "title"} == set(s) for s in steps.summary())


def test_step_ids_are_unique():
    steps = ThinkingSteps()
    steps.consume("First: a\nSecond: b\nThird: c\n")

    ids = [s["step_id"] for s in steps.summary()]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Reasoning is carried separately, end to end
# ---------------------------------------------------------------------------


def test_the_chunk_type_has_somewhere_to_put_reasoning():
    from archihub.api.aiservices.dialects.base import ChatChunk

    assert ChatChunk().reasoning == ""


@pytest.mark.parametrize(
    ("payload", "expected_delta", "expected_reasoning"),
    [
        ({"choices": [{"delta": {"content": "hi"}}]}, "hi", ""),
        ({"choices": [{"delta": {"reasoning_content": "hmm"}}]}, "", "hmm"),
        ({"choices": [{"delta": {"reasoning": "hmm"}}]}, "", "hmm"),
        ({"choices": [{"delta": {"content": "hi", "reasoning": "hmm"}}]}, "hi", "hmm"),
    ],
)
def test_openai_compatible_reasoning_is_not_mixed_into_the_answer(
    payload, expected_delta, expected_reasoning
):
    """Two spellings in the wild; absence of both is the normal case."""
    from archihub.api.aiservices.dialects.openai_compat import OpenAICompatibleDialect

    chunk = OpenAICompatibleDialect.__dict__["_chunk"](None, payload)

    assert chunk.delta == expected_delta
    assert chunk.reasoning == expected_reasoning


def test_google_keeps_thought_parts_out_of_the_answer():
    """The defect this found: `_text` took every part, thoughts included."""
    from archihub.api.aiservices.dialects.google import _text, _thought_text

    candidate = {
        "content": {
            "parts": [
                {"text": "Let me think about this. ", "thought": True},
                {"text": "The interview covers three topics."},
            ]
        }
    }

    assert _text(candidate) == "The interview covers three topics."
    assert _thought_text(candidate) == "Let me think about this. "


def test_google_with_no_thought_parts_is_unchanged():
    from archihub.api.aiservices.dialects.google import _text, _thought_text

    candidate = {"content": {"parts": [{"text": "plain answer"}]}}

    assert _text(candidate) == "plain answer"
    assert _thought_text(candidate) == ""
