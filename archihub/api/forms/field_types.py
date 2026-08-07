"""The built-in form field-type catalogue.

Port of ``_get_all_fields_types`` in ``app/api/forms/services.py``, lifted into
its own module because it is data, not logic.

Ids are a wire contract: they are stored on every form field, matched by
``Form.tsx``'s field-type dispatch in the frontend, and referenced by plugins
that contribute their own types through the ``get_fields_types`` hook. Renaming
one would orphan every field already using it.

``userslit`` is a typo for "user list" that has been in the data since the
beginning. It is preserved deliberately - correcting the spelling would
invalidate every stored field using it, and a migration is the only safe way to
change it.

Labels are the English source strings; they are translated per request in
``services.get_all_fields_types`` because the instance locale can change without
a restart.
"""

from __future__ import annotations

FIELD_TYPES: tuple[dict[str, str], ...] = (
    {"id": "text", "label": "Text"},
    {"id": "text-area", "label": "Text area"},
    {"id": "number", "label": "Number"},
    {"id": "simple-date", "label": "Date"},
    {"id": "select", "label": "Select"},
    {"id": "select-multiple2", "label": "Select multiple"},
    {"id": "checkbox", "label": "Checkbox"},
    {"id": "file", "label": "File"},
    {"id": "repeater", "label": "Repeater"},
    {"id": "separator", "label": "Separator"},
    {"id": "author", "label": "Author"},
    {"id": "location", "label": "Location"},
    # sic - see module docstring.
    {"id": "userslit", "label": "User list"},
)
