"""Content-type request models.

Port of ``app/api/types/models.py``, with one deliberate removal.

THE LEGACY MODEL CARRIED A LATENT `_id` TRAP:

    class PostType(BaseModel):
        id: str = Field(default_factory=uuid.uuid4, alias="_id")

Three things are wrong with that line, and they cancel each other out by luck:

1. ``uuid.uuid4`` returns a ``UUID``, not a ``str``. Pydantic does not validate
   ``default_factory`` output (``validate_default`` is off by default), so the
   field holds a UUID in spite of its annotation.
2. ``model_dump()`` keys by field name, not alias, so it emits ``id`` - never
   ``_id`` - unless called with ``by_alias=True``.
3. ``DatabaseHandler.insert_record`` calls ``model_dump(exclude_unset=True)``,
   and since the id is never explicitly set it is dropped entirely.

So the field is inert, and MongoDB assigns a proper ``ObjectId`` (verified: all
`post_types` documents have ObjectId ids). But anyone "tidying" this by removing
``exclude_unset`` or adding ``by_alias=True`` would immediately start writing
UUID primary keys, breaking every ``ObjectId(...)`` lookup against the
collection. The field is simply not reproduced here - MongoDB generates ids.

Response models are intentionally NOT declared. FastAPI's ``response_model``
*filters* the payload to declared fields, so anything undeclared silently
disappears while still returning 200. Per PLAN_FASTAPI.md section 7, responses
stay unfiltered until a route's diff-harness run confirms field-for-field parity.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PostTypeCreate(BaseModel):
    """Body of ``POST /types``.

    Validation stays deliberately permissive: the legacy endpoint accepted a
    body with only ``name``/``description``/``slug`` meaningfully populated and
    tolerated extras. Tightening it here would reject requests the current
    frontend sends.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    description: str = ""
    # Required as a KEY by the legacy route (it does `body['slug']`), but may be
    # empty - the route then derives a slug from the name. Defaulting to "" keeps
    # a body that omits it working rather than raising KeyError.
    slug: str = ""
    metadata: str | None = None
    icon: str | None = None
    hierarchical: bool = False
    parentType: list[dict] = Field(default_factory=list)
    editRoles: list[str] | None = None
    viewRoles: list[str] | None = None
    isArticle: bool = False
    post_count: int = 0


class PostTypeUpdate(BaseModel):
    """Body of ``PUT /types/{slug}``. Every field optional - it is a patch."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    hierarchical: bool | None = None
    parentType: list[dict] | None = None
    metadata: str | None = None
    editRoles: list[str] | None = None
    viewRoles: list[str] | None = None
    isArticle: bool | None = None


class TypeVizRequest(BaseModel):
    """Body of ``POST /types/moreinfo``."""

    model_config = ConfigDict(extra="allow")

    slug: str
    type: str


class TypesInfoRequest(BaseModel):
    """Body of the public ``POST /types/info``."""

    model_config = ConfigDict(extra="allow")

    types: list[str] | None = None
    data: Any | None = None
