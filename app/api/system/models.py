import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de opciones del sistema
class Option(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    data: dict = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Opción",
                "data": {
                    "key": "value"
                }
            }
        }

# Modelo para la actualización de opciones del sistema
class OptionUpdate(BaseModel):
    data: Optional[dict]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "data": {
                    "key": "value"
                }
            }
        }