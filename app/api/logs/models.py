import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de logs
class Log(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    username: str
    action: str
    date: str
    metadata: Optional[dict] = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "username": "John Doe",
                "action": "User login",
                "date": "2021-08-23 12:00:00",
            }
        }

# Modelo para la actualización de logs
class LogUpdate(BaseModel):
    username: Optional[str]
    action: Optional[str]
    date: Optional[str]
    metadata: Optional[dict] = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "username": "John Doe",
                "action": "User login",
                "date": "2021-08-23 12:00:00",
            }
        }