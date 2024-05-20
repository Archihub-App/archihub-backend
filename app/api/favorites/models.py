import uuid
from typing import Optional
from pydantic import BaseModel, Field

# Modelo para el registro de favoritos
class Favorite(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    user_id: str
    standard_id: str

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "user_id": "user_id",
                "standard_id": "standard_id"
            }
        }

# Modelo para la actualización de favoritos
class FavoriteUpdate(BaseModel):
    user_id: Optional[str]
    standard_id: Optional[str]

    class Config:
        allow_population_by_field_name = True
        schema_extra = {
            "example": {
                "user_id": "user_id",
                "standard_id": "standard_id"
            }
        }