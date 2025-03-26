import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

class UserTask(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    resourceId: str
    recordId: str = None
    user: str
    status: str = "pending"
    comment: list[dict]
    createdAt: datetime
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "resourceId": "123456",
                "user": "johndoe",
                "comment": [{"key": "value"}]
            }
        }
        
class UserTaskUpdate(BaseModel):
    user: Optional[str] = None
    status: Optional[str] = None
    comment: Optional[list[dict]] = None
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "resourceId": "123456",
                "user": "johndoe",
                "comment": [{"key": "value"}]
            }
        }