import uuid
from typing import Optional
from typing import Any
from pydantic import BaseModel, Field

# Modelo para el registro de poligonos geográficos
class Polygon(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    properties: dict
    geometry: dict
    type: str = 'Feature'

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "properties": {
                    "name": "Polígono",
                    "description": "Descripción del polígono"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-74.297, 4.570],
                            [-74.297, 4.571],
                            [-74.296, 4.571],
                            [-74.296, 4.570],
                            [-74.297, 4.570]
                        ]
                    ]
                }
            }
        }

# Modelo para la actualización de poligonos geográficos
class PolygonUpdate(BaseModel):
    properties: Optional[dict]
    geometry: Optional[dict]

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "properties": {
                    "name": "Polígono",
                    "description": "Descripción del polígono"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-74.297, 4.570],
                            [-74.297, 4.571],
                            [-74.296, 4.571],
                            [-74.296, 4.570],
                            [-74.297, 4.570]
                        ]
                    ]
                }
            }
        }