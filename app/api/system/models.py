import uuid
from typing import Optional
from typing import Any, Dict
from pydantic import BaseModel, Field

# Modelo para el registro de opciones del sistema
class Option(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    data: Dict[str, Any]
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
    data: Optional[Dict[str, Any]] = None
    plugins_settings: Optional[Dict[str, Any]] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "data": {
                    "key": "value"
                }
            }
        }