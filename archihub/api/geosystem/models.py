"""The stored form of an administrative boundary.

Port of ``app/api/geosystem/models.py``. One change, and it is not cosmetic.

``id`` had ``default_factory=uuid.uuid4``, declared ``str`` — so the default was
a ``UUID`` **object**, not a string, and Pydantic v2 does not coerce a default
produced by a factory. Every shape's ``_id`` therefore went into MongoDB as a
BSON binary UUID while the type said otherwise, and anything comparing that id
to a string got no match. Here the factory produces the string the annotation
promises. Existing stored shapes keep their binary ids and still read back
fine; the loader replaces a level wholesale, so a reload normalises them.
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
