import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de listados
class List(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    slug: str
    description: str
    fields: list[dict]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Listado",
                "description": "Listado de registro de datos"
            }
        }

# Modelo para la actualización de listados
class ListUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]
    fields: Optional[list[dict]]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Listado",
                "description": "Listado de registro de datos"
            }
        }