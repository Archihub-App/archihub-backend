import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

class UserTask(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    resourceId: str
    user: str
    status: str = "pending"
    comment: list[dict]
    createdAt: datetime
    
    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "resourceId": "123456",
                "user": "johndoe",
                "comment": [{"key": "value"}]
            }
        }
        
class UserTaskUpdate(BaseModel):
    user: Optional[str]
    status: Optional[str]
    comment: Optional[list[dict]]
    
    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "resourceId": "123456",
                "user": "johndoe",
                "comment": [{"key": "value"}]
            }
        }