import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de un recurso
class Record(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    mime: str = None
    metadata: dict = None
    parents: list[dict] = None
    processing: dict = None
    filepath: str
    size: int
    hash: str
    name: str

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