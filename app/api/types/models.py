import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de tipos de contenido
class PostType(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    description: str
    slug: str
    post_count: int = 0
    metadata: str = None
    icon: str = None
    hierarchical: bool = False
    # parentType array of dict
    parentType: list[dict] = []
    editRoles: list[str] = None
    viewRoles: list[str] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "Post",
                "description": "Publicación de texto"
            }
        }

# Modelo para la actualización de tipos de contenido
class PostTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    hierarchical: Optional[bool] = None
    parentType: Optional[list[dict]] = None
    metadata: Optional[str] = None
    editRoles: Optional[list[str]] = None
    viewRoles: Optional[list[str]] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "Post",
                "description": "Publicación de texto"
            }
        }
