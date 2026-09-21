"""Every field kind the forms builder can create is understood downstream.

The forms builder fills its picker from `GET /forms/fields`, so the ids in
`api/forms/field_types.py` are exactly the values that end up stored on real
metadata fields. Three separate tables then consume those ids - the Elasticsearch
mapping, the metadata validators, and `Form.tsx`'s field dispatch in the frontend
- and each was written independently.

They can drift silently. The catalogue offers **`userslit`** (a typo for "user
list", kept because it is the stored wire value); a consumer spelling it
**`userslist`** would neither render, validate nor map a User list field, while
its value still reached Elasticsearch with an inferred type.
"""

from __future__ import annotations

import pytest

from archihub.api.forms.field_types import FIELD_TYPES as CATALOGUE
from archihub.api.resources.validation import CLEARED_WHEN_HIDDEN, VALIDATORS
from archihub.api.search.mapping import FIELD_TYPES as MAPPING

CATALOGUE_IDS = sorted({entry["id"] for entry in CATALOGUE})

#: Kinds that legitimately have no index mapping, with the reason.
NOT_INDEXED = {
    "file": "a stored attachment descriptor, popped from the mapping on purpose",
    "separator": "presentational; it holds no value",
}

#: Kinds that legitimately have no top-level validator.
NOT_VALIDATED = {
    "file": "files are validated by validate_files, against the type's file rules",
    "separator": "presentational; it holds no value",
    "repeater": "rows are validated per subfield, via REPEATER_VALIDATORS",
}


@pytest.mark.parametrize("kind", CATALOGUE_IDS)
def test_every_offered_field_kind_can_be_indexed(kind):
    if kind in NOT_INDEXED:
        pytest.skip(NOT_INDEXED[kind])

    assert kind in MAPPING, (
        f"the forms builder offers {kind!r} but api/search/mapping.py has no entry "
        "for it, so a field of that kind is left to Elasticsearch's dynamic "
        "inference. Add a mapping, or list it in NOT_INDEXED with the reason."
    )


@pytest.mark.parametrize("kind", CATALOGUE_IDS)
def test_every_offered_field_kind_can_be_validated(kind):
    if kind in NOT_VALIDATED:
        pytest.skip(NOT_VALIDATED[kind])

    assert kind in VALIDATORS, (
        f"the forms builder offers {kind!r} but api/resources/validation.py has no "
        "validator for it, so whatever a client sends is stored unchecked. Add "
        "one, or list it in NOT_VALIDATED with the reason."
    )


def test_the_user_list_typo_is_the_spelling_the_consumers_key_on():
    """The specific drift this file was written for.

    `userslit` is what the catalogue offers and therefore what is stored. It is
    NOT corrected at the source - the id is a wire contract and renaming it
    would orphan every field already using it - so the consumers must accept it.
    """
    assert "userslit" in {entry["id"] for entry in CATALOGUE}
    assert "userslit" in MAPPING
    assert "userslit" in VALIDATORS
    assert "userslit" in CLEARED_WHEN_HIDDEN


def test_a_conditional_user_list_field_is_cleared_like_the_other_value_kinds():
    """Hiding a field must clear it, or a stale value is saved invisibly."""
    assert CLEARED_WHEN_HIDDEN["userslit"] == []
