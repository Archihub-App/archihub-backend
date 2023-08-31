import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de estándares de metadatos
class Form(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    slug: str
    description: str
    fields: list[dict] = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Formulario",
                "description": "Formulario de registro de datos"
            }
        }

# Modelo para la actualización de estándares de metadatos
class FormUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]
    fields: Optional[list[dict]]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Formulario",
                "description": "Formulario de registro de datos"
            }
        }