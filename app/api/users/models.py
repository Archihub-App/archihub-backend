import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de usuarios
class User(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    username: str
    password: str
    compromise: bool = False
    photo: str = None
    token: str = ""
    adminToken: str = ""
    nodeToken: str = ""
    roles: list[str] = None
    accessRights: list[str] = None
    requests: int = 0
    lastRequest: datetime = None
    favorites: list[str] = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@loquesea.com"
            }
        }

# Modelo para la actualización de usuarios
class UserUpdate(BaseModel):
    name: Optional[str]
    password: Optional[str]
    compromise: Optional[bool]
    photo: Optional[str] = None
    roles: Optional[list[str]] = None
    accessRights: Optional[list[str]] = None
    token: Optional[str] = None
    adminToken: Optional[str] = None
    nodeToken: Optional[str] = None
    requests: Optional[int] = None
    lastRequest: Optional[datetime] = None
    favorites: Optional[list[str]] = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@loquesea.com"
            }
        }