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
    vizToken: str = ""
    roles: list[str] = None
    accessRights: list[str] = None
    requests: int = 0
    lastRequest: datetime = None
    favorites: list[dict] = None
    loginType: str = "local"
    verified: bool = False

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@loquesea.com"
            }
        }

# Modelo para la actualización de usuarios
class UserUpdate(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None
    compromise: Optional[bool] = None
    photo: Optional[str] = None
    roles: Optional[list[str]] = None
    accessRights: Optional[list[str]] = None
    token: Optional[str] = None
    adminToken: Optional[str] = None
    nodeToken: Optional[str] = None
    vizToken: Optional[str] = None
    requests: Optional[int] = None
    lastRequest: Optional[datetime] = None
    favorites: Optional[list[dict]] = None
    loginType: Optional[str] = None
    verified: Optional[bool] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@loquesea.com"
            }
        }