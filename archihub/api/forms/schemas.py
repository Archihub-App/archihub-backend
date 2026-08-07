"""Metadata-form request models.

Port of ``app/api/forms/models.py``, minus the inert UUID ``_id`` default (see
``api/types/schemas.py`` for why that field was harmless only by accident).

``fields`` is deliberately left as ``list[dict]`` rather than being modelled
field-by-field. A form field is a small open-ended schema whose valid keys depend
on its ``type`` (``repeater`` carries ``subfields``, ``file`` carries
``filetag``, conditional fields carry ``conditionField``/``conditionType``, and
plugins contribute their own types at runtime). Pinning that down in Pydantic
would reject payloads the current builder sends. Validation stays where it
already lives - ``services.validate_form`` - which understands those
inter-dependencies.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FormCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    description: str = ""
    # Optional: derived from `name` when absent or empty.
    slug: str = ""
    fields: list[dict] = Field(default_factory=list)


class FormUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    description: str | None = None
    slug: str | None = None
    fields: list[dict] | None = None
