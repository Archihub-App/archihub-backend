"""The stored form of an administrative boundary.

``id`` defaults to a UUID *string*, as its annotation says: Pydantic does not
coerce a default produced by a factory, so a bare ``uuid.uuid4`` would store a
binary UUID. Shapes stored with binary ids still read back; the loader replaces
a level wholesale, so a reload normalises them.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class Polygon(BaseModel):
    """One boundary feature, as GeoJSON plus its administrative properties."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    properties: dict
    geometry: dict
    type: str = "Feature"


class PolygonUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    properties: dict | None = None
    geometry: dict | None = None
