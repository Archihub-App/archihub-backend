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
    metadata: list[str] = None
    icon: str = None
    hierarchical: bool = False
    parentType: str = None

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

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "Post",
                "description": "Publicación de texto"
            }
        }
