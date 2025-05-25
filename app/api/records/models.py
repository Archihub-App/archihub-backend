import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de un recurso
class Record(BaseModel):
    id: str =  Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    mime: Optional[str] = None
    metadata: dict = None
    parents: list[dict] = None
    parent: list[dict] = None
    processing: dict = None
    filepath: str
    size: Optional[int] = None
    hash: str
    name: str
    status: str = 'uploaded'
    displayName: str = None
    accessRights: str = None
    favCount: int = 0
    updatedAt: datetime
    updatedBy: str

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
class RecordUpdate(BaseModel):
    metadata: Optional[dict] = None
    parents: Optional[list[dict]] = None
    processing: Optional[dict] = None
    name: Optional[str] = None
    parent: Optional[list[dict]] = None
    status: Optional[str] = None
    displayName: Optional[str] = None
    accessRights: Optional[str] = None
    favCount: Optional[int] = None
    updatedBy: str
    updatedAt: datetime

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