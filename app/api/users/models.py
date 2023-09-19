import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de usuarios
class User(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    email: str
    password: str
    accessLevel: str = "Public"
    compromise: bool = False
    photo: str = None
    roles: list[str] = None

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
    email: Optional[str]
    password: Optional[str]
    accessLevel: Optional[str]
    compromise: Optional[bool]
    photo: Optional[str] = None
    roles: Optional[list[str]] = None

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@loquesea.com"
            }
        }