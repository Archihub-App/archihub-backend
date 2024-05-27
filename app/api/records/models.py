import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de un recurso
class Record(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
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
class RecordUpdate(BaseModel):
    metadata: Optional[dict]
    parents: Optional[list[dict]]
    processing: Optional[dict]
    name: Optional[str]
    parent: Optional[list[dict]]
    status: Optional[str]
    displayName: Optional[str]
    accessRights: Optional[str]
    favCount: Optional[int]

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