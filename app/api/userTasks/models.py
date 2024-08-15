import uuid
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de tareas
class UserTask(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    user_id: str
    resource_id: str
    status: str
    description: str
    comments: list[dict] = None
    created_by: str
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "user_id": "60b9c1e3f7c3a4b4e3e2c0d3",
                "task_id": "60b9c1e3f7c3a4b4e3e2c0d3",
                "status": "pending"
            }
        }

# Modelo para la actualización de tareas
class UserTaskUpdate(BaseModel):
    status: Optional[str]
    comments: Optional[list[dict]] = None
    updated_at: Optional[datetime] = datetime.now()

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "status": "pending"
            }
        }