import uuid
from typing import Optional
from typing import Any
from pydantic import BaseModel, Field

# Modelo para el registro de opciones del sistema
class Task(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    taskId: str
    name: str
    user: str
    status: str
    resultType: str
    result: str

    class Config:
        allow_population_by_field_name = True

# Modelo para la actualización de opciones del sistema
class TaskUpdate(BaseModel):
    status: str

    class Config:
        allow_population_by_field_name = True