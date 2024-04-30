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
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Post",
                "description": "Publicación de texto"
            }
        }

# Modelo para la actualización de tipos de contenido
class PostTypeUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]
    icon: Optional[str]
    hierarchical: Optional[bool]
    parentType: Optional[list[dict]]
    metadata: Optional[str]
    editRoles: Optional[list[str]]
    viewRoles: Optional[list[str]]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Post",
                "description": "Publicación de texto"
            }
        }
