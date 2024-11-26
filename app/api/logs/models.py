import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de logs
class Log(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    username: str
    action: str
    date: datetime
    metadata: Optional[dict] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "username": "John Doe",
                "action": "User login",
                "date": "2021-08-23 12:00:00",
            }
        }
