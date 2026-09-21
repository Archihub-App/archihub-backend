"""Content-type request models.

NO `_id` FIELD, deliberately: MongoDB generates ids. A default such as
``Field(default_factory=uuid.uuid4, alias="_id")`` would write UUID primary keys
the moment it was dumped with ``by_alias=True``, breaking every
``ObjectId(...)`` lookup against the collection.

Response models are intentionally NOT declared. FastAPI's ``response_model``
*filters* the payload to declared fields, so anything undeclared silently
disappears while still returning 200.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PostTypeCreate(BaseModel):
    """Body of ``POST /types``.

    Validation stays deliberately permissive: extra fields are tolerated, since
    the frontend sends them.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    description: str = ""
    # May be empty or absent - the route then derives a slug from the name.
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
