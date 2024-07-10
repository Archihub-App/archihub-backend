import uuid
from typing import Optional
from pydantic import BaseModel, Field

class View(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    slug: str
    description: str
    parent: str
    root: str
    visible: list[str]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@test.com"
            }
        }

class ViewUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]
    parent: Optional[str]
    root: Optional[str]
    visible: Optional[list[str]]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@test.com"
            }
        }