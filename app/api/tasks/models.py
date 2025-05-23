import uuid
from typing import Optional
from typing import Any
from pydantic import BaseModel, Field
from datetime import datetime

# Modelo para el registro de opciones del sistema
class Task(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    taskId: str
    name: str
    user: str
    status: str
    resultType: str
    result: Optional[Any] = None
    date: datetime
    params: Optional[Any] = None

    class Config:
        populate_by_name = True

# Modelo para la actualización de opciones del sistema
class TaskUpdate(BaseModel):
    status: Optional[str] = None
    result: Optional[Any] = None

    class Config:
        populate_by_name = True