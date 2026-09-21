"""List request models.

No ``_id`` field: MongoDB generates ids (see ``api/types/schemas.py``).

A list's ``options`` are stored as a separate ``options`` collection; the list
document holds an ordered array of their string ids. That indirection is why the
update path below is more involved than a plain patch.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OptionInput(BaseModel):
    """One option inside a list-update payload.

    Three shapes arrive here and each means something different:

    * ``{"term": "..."}``                      -> a new option, to be created
    * ``{"id": "...", "term": "..."}``         -> an existing option, to update
    * ``{"id": "...", "deleted": true}``       -> drop it from the list
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    term: str | None = None
    deleted: bool = False


class ListCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    description: str = ""
    options: list[OptionInput] = Field(default_factory=list)


class ListUpdate(BaseModel):
    """Body of ``PUT /lists/{id}``. All fields optional - it is a patch.

    ``options`` is optional: a patch may only rename a list.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    description: str | None = None
    options: list[OptionInput] | None = None
