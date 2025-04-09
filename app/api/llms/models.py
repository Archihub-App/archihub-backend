import uuid
from typing import Optional
from pydantic import BaseModel, Field

class LlmProvider(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    name: str
    provider: str
    key: str
    
    class Config:
        populate_by_name = True
        
class LlmProviderUpdate(BaseModel):
    name: Optional[str] = None
    key: Optional[str] = None
    
    class Config:
        populate_by_name = True