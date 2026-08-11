"""The Elasticsearch mapping for the `resources` index.

Port of ``transform_dict_to_mapping`` plus the fixed block that follows it in
``app/api/system/services.py:622`` (``regenerate_index``). The two were written
as one 130-line function that built a mapping and dispatched a Celery task; they
are separated here because the mapping is a pure function of the stored schema
and is the only part worth testing.

WHERE THE SCHEMA COMES FROM. The `resources-schema` document in the `system`
collection is maintained by the forms builder: it records, for every metadata
field any content type declares, what kind of field it is. This turns that into
Elasticsearch field types. A field kind this does not recognise is DROPPED from
the mapping rather than guessed at - Elasticsearch would otherwise infer a type
from the first document that carried it, and the guess sticks until the next
regeneration.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: A text field, plus an unanalysed sub-field for sorting and aggregation.
_TEXT_WITH_KEYWORD = {
    "type": "text",
    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
}

_ANALYSED_TEXT_WITH_KEYWORD = {
    "type": "text",
    "analyzer": "analyzer_spanish",
    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
}

_RELATION = {
    "type": "object",
    "properties": {"id": _TEXT_WITH_KEYWORD, "post_type": _TEXT_WITH_KEYWORD},
}

#: Field kind -> Elasticsearch mapping. Anything absent is intentionally not
#: indexed; see the module docstring.
FIELD_TYPES: dict[str, dict] = {
    "text": _ANALYSED_TEXT_WITH_KEYWORD,
    "text-area": {"type": "text", "analyzer": "analyzer_spanish"},
    "select": {"type": "keyword"},
    "simple-date": {"type": "date"},
    "location": {"type": "geo_shape"},
    "relation": _RELATION,
    "repeater": {"type": "object"},
    "checkbox": {"type": "boolean"},
    "number": {"type": "float"},
    "userslist": {"type": "keyword"},
    "select-multiple2": {"type": "keyword", "ignore_above": 256},
    "author": {"type": "keyword", "ignore_above": 256},
}

#: Fields the indexer writes itself, whatever the schema says. `article` holds
#: the flattened text of an article's paragraphs; `files` is a count, not a list.
SYSTEM_FIELDS: dict[str, dict] = {
    "article": {"type": "text"},
    "post_type": _TEXT_WITH_KEYWORD,
    "status": _TEXT_WITH_KEYWORD,
    "parents": _RELATION,
    "parent": _RELATION,
    "ident": _TEXT_WITH_KEYWORD,
    "createdAt": {"type": "date"},
    "files": {"type": "integer"},
}


def _map_field(definition: object, path: str) -> dict | None:
    """One schema entry to one Elasticsearch field definition."""
    if not isinstance(definition, dict):
        # The legacy version raised ValueError here, inside the try/except of a
        # route, so one malformed schema entry became a 500 with no indication
        # of which entry. Skipping it costs that one field's searchability and
        # names it in the log.
        logger.warning("Ignoring unusable schema entry at %r: %r", path, definition)
        return None

    kind = definition.get("type")
    if isinstance(kind, str):
        mapped = FIELD_TYPES.get(kind)
        if mapped is None:
            logger.info("No index mapping for field kind %r at %r; not indexed", kind, path)
            return None
        # Copied, not shared: callers mutate the returned mapping (the `file`
        # key is popped out of it below), and the module-level constants must
        # not be edited by that.
        return dict(mapped)

    # No `type` key: a nested group of fields, mapped recursively.
    nested = {
        key: mapped
        for key, value in definition.items()
        if (mapped := _map_field(value, f"{path}.{key}" if path else key)) is not None
    }
    return {"type": "object", "properties": nested} if nested else None


def build_resources_mapping(schema: dict) -> dict:
    """The full ``mappings`` body for the resources index.

    ``schema`` is the ``data`` of the `resources-schema` system document.
    """
    properties = {
        key: mapped
        for key, value in (schema or {}).items()
        if (mapped := _map_field(value, key)) is not None
    }

    # `file` is a stored attachment descriptor, not searchable metadata. The
    # legacy code popped it after building, which is the same result.
    properties.pop("file", None)
    properties.update(SYSTEM_FIELDS)

    return {"properties": properties}


#: Mapping for the geometry index. Fixed - it does not depend on any user
#: schema, since boundary shapes are loaded from bundled data.
SHAPES_MAPPING: dict = {
    "properties": {
        "geometry": {"type": "geo_shape"},
        "properties": {
            "type": "object",
            "properties": {
                "admin_level": {"type": "integer"},
                "ident": {"type": "keyword"},
                # No `ignore_above` on these two, matching the legacy mapping:
                # a boundary name is short, and capping it would silently stop
                # indexing the keyword form of any that is not.
                "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "parent_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "parent": {"type": "keyword"},
            },
        },
    }
}
