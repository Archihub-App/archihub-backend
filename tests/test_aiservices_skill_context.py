"""Selected skills actually shape the prompt.

The frontend asks for a skill in TWO ways at once — it sends `applied_skills`
and it prefixes the message with a literal `\\path` token per skill:

    requestMessage = [...appliedSkills.map(s => `\\${s}`), trimmed].join(' ')

So a backend reading only the field leaves the token in the message, and the
model receives an unexplained `\\research/summarise` with no instruction
attached. Reading only the tokens loses a selection made without typing. Both
are read, the tokens are stripped, and the union is applied.
"""

from __future__ import annotations

import pytest

from archihub.api.aiservices import skill_context

SKILL = {
    "path": "research/summarise.md",
    "command": "research/summarise",
    "name": "summarise",
    "title": "Summarise a source",
    "content": "Produce five bullet points.",
}


class FakeMongo:
    def __init__(self, rows):
        self.rows = rows
        self.filters: list[dict] = []

    def get_record(self, collection, filters, fields=None):
        self.filters.append(filters)
        wanted = {
            clause[field]
            for clause in filters.get("$or", [])
            for field in clause
        }
        for row in self.rows:
            if wanted & {row.get(k) for k in ("path", "command", "name", "title")}:
                return row
        return None


@pytest.fixture
def skills(monkeypatch):
    fake = FakeMongo([SKILL])
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Reading the request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected_text", "expected_tokens"),
    [
        (r"\research/summarise give me a summary", "give me a summary", ["research/summarise"]),
        ("give me a summary", "give me a summary", []),
        (r"\a \b question", "question", ["a", "b"]),
        (r"the path is C:\Users\me", r"the path is C:\Users\me", []),
    ],
)
def test_inline_tokens_are_stripped_from_the_message(message, expected_text, expected_tokens):
    """The last case matters: a Windows path is not a skill request."""
    cleaned, tokens = skill_context.strip_inline(message)

    assert cleaned == expected_text
    assert tokens == expected_tokens


def test_a_skill_is_found_by_any_of_its_names(skills):
    for identifier in ("research/summarise.md", "research/summarise", "summarise"):
        assert skill_context.lookup(identifier) is not None, identifier


def test_a_traversal_identifier_simply_matches_nothing(skills):
    """Refused by matching no skill, not by an error that confirms the attempt."""
    assert skill_context.lookup("../../etc/passwd") is None


def test_an_unknown_skill_is_skipped_rather_than_failing(skills):
    """A stray backslash in prose must not cost the user their answer."""
    cleaned, resolved = skill_context.resolve(r"\nonsense tell me about it", None)

    assert cleaned == "tell me about it"
    assert resolved == []


def test_the_field_and_the_inline_token_resolve_to_one_skill(skills):
    """The frontend sends both for the same selection."""
    _cleaned, resolved = skill_context.resolve(
        r"\research/summarise summarise this", ["research/summarise"]
    )

    assert len(resolved) == 1


def test_a_stored_skill_dict_is_accepted(skills):
    """`applied_skills` round-trips through the database as dicts."""
    _cleaned, resolved = skill_context.resolve("summarise this", [{"path": "research/summarise.md"}])

    assert len(resolved) == 1


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


def _messages():
    return [
        {"role": "system", "content": "you are an assistant"},
        {"role": "user", "content": "Transcription:\n\nthe text"},
        {"role": "assistant", "content": "I have read it."},
        {"role": "user", "content": r"\research/summarise summarise this"},
    ]


def test_the_context_lands_on_the_last_user_turn(skills):
    prepared, resolved = skill_context.apply_to(
        _messages(), r"\research/summarise summarise this", ["research/summarise"]
    )

    assert len(resolved) == 1
    assert "Produce five bullet points." in prepared[-1]["content"]
    # Not the system turn: a skill modifies this request, it does not change
    # what the assistant is.
    assert "Produce five bullet points." not in prepared[0]["content"]


def test_the_token_does_not_survive_into_the_users_question(skills):
    """Scoped to the question, not the whole turn.

    The rendered header legitimately contains `\\command` - that is how the
    skill is named to the model. What must not survive is the token sitting in
    the user's own sentence, unexplained.
    """
    prepared, _resolved = skill_context.apply_to(
        _messages(), r"\research/summarise summarise this", ["research/summarise"]
    )

    question = prepared[-1]["content"].split("User request:", 1)[1]
    assert "\\research/summarise" not in question
    assert question.strip() == "summarise this"


def test_earlier_turns_are_untouched(skills):
    prepared, _resolved = skill_context.apply_to(
        _messages(), r"\research/summarise x", ["research/summarise"]
    )

    assert prepared[1]["content"] == "Transcription:\n\nthe text"


def test_the_original_messages_are_not_mutated(skills):
    original = _messages()
    skill_context.apply_to(original, r"\research/summarise x", ["research/summarise"])

    assert original[-1]["content"] == r"\research/summarise summarise this"


def test_no_skills_means_no_change(skills):
    original = _messages()

    prepared, resolved = skill_context.apply_to(original, "just a question", None)

    assert resolved == []
    assert prepared is original


def test_a_multipart_turn_keeps_one_text_block(skills):
    """A provider that expects one text part per turn must still get one."""
    messages = [
        {"role": "system", "content": "s"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA"}},
                {"type": "text", "text": r"\research/summarise what is this"},
            ],
        },
    ]

    prepared, resolved = skill_context.apply_to(
        messages, r"\research/summarise what is this", ["research/summarise"]
    )

    parts = prepared[-1]["content"]
    assert len(resolved) == 1
    assert sum(1 for p in parts if p["type"] == "text") == 1
    assert sum(1 for p in parts if p["type"] == "image_url") == 1
    assert "Produce five bullet points." in next(p for p in parts if p["type"] == "text")["text"]


def test_an_oversized_skill_body_is_capped(monkeypatch, skills):
    """Skill files are operator-authored and have no size limit of their own."""
    monkeypatch.setattr(skill_context, "MAX_CONTEXT_CHARS", 50)

    rendered = skill_context.render([{**SKILL, "content": "x" * 5000}])

    assert len(rendered) < 300


def test_the_rendered_context_names_the_skill(skills):
    rendered = skill_context.render([SKILL])

    assert "Summarise a source" in rendered
    assert "Produce five bullet points." in rendered
    assert rendered.startswith(skill_context.PREAMBLE)
