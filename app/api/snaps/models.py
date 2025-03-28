import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de snaps
class Snap(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    user: str
    record_id: str
    record_name: str
    type: str
    data: dict
    createdAt: datetime

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "user": "user",
                "type": "snap_type",
                "data": {
                    "key": "value"
                }
            }
        }

# Modelo para la actualización de snaps
class SnapUpdate(BaseModel):
    data: Optional[dict] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "user": "user",
                "type": "snap_type",
                "data": {
                    "key": "value"
                }
            }
        }