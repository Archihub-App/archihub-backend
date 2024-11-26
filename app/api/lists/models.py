import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de listados
class List(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    description: str
    options: list[str]

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "Listado",
                "description": "Listado de registro de datos"
            }
        }

# Modelo para la actualización de listados
class ListUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]
    options: Optional[list[str]]

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "Listado",
                "description": "Listado de registro de datos"
            }
        }

# Modelo para el registro de opciones
class Option(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    term: str

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "term": "Opción"
            }
        }

# Modelo para la actualización de opciones
class OptionUpdate(BaseModel):
    term: Optional[str]

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "term": "Opción"
            }
        }