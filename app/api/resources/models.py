import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de un recurso
class Resource(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    post_type: str
    metadata: dict
    parents: list[dict] = None
    parent: dict = None
    files: list[dict] = []
    ident: str
    status: str = 'created'

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "post_type": "post",
                "metadata": {
                },
                "files": [
                    {
                        "name": "imagen.jpg",
                        "file": "https://url.com/imagen.jpg"
                    }
                ],
                "ident": "123456789"
            }
        }

# Modelo para la actualización de un recurso
class ResourceUpdate(BaseModel):
    post_type: Optional[str]
    metadata: Optional[dict]
    files: Optional[list[dict]]
    parents: Optional[list[dict]]
    parent: Optional[dict]
    status: Optional[str]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "post_type": "post",
                "metadata": {
                },
                "files": [
                    {
                        "name": "imagen.jpg",
                        "file": "https://url.com/imagen.jpg"
                    }
                ],
                "ident": "123456789"
            }
        }