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
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@test.com"
            }
        }

class ViewUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent: Optional[str] = None
    root: Optional[str] = None
    visible: Optional[list[str]] = None

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "johndoe@test.com"
            }
        }