import uuid
from typing import Optional
from typing import Any
from pydantic import BaseModel, Field

# Modelo para el registro de opciones del sistema
class Option(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    data: dict = None
    label: str = None
    plugins_settings: dict = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "Opción",
                "data": {
                    "key": "value"
                }
            }
        }

# Modelo para la actualización de opciones del sistema
class OptionUpdate(BaseModel):
    data: Any
    plugins_settings: Optional[dict] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "data": {
                    "key": "value"
                }
            }
        }