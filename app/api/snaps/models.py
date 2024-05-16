import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de snaps
class Snap(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    user_id: str
    type: str
    data: dict

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "user_id": "user_id",
                "type": "snap_type",
                "data": {
                    "key": "value"
                }
            }
        }

# Modelo para la actualización de snaps
class SnapUpdate(BaseModel):
    user_id: Optional[str]
    type: Optional[str]
    data: Optional[dict]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "user_id": "user_id",
                "type": "snap_type",
                "data": {
                    "key": "value"
                }
            }
        }