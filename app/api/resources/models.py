import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de un recurso
class Resource(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    post_type: str
    metadata: dict
    parents: list[dict] = None
    parent: dict = None
    filesObj: list[dict] = []
    ident: str
    status: str = 'created'
    accessRights: Optional[str] = None
    createdBy: str = None
    createdAt: datetime
    favCount: int = 0

    class Config:
        populate_by_name = True
        json_schema_extra = {
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
    post_type: Optional[str] = None
    metadata: Optional[dict] = None
    filesObj: Optional[list[dict]] = None
    parents: Optional[list[dict]] = None
    parent: Optional[dict] = None
    status: Optional[str] = None
    accessRights: Optional[str] = None
    favCount: Optional[int] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
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