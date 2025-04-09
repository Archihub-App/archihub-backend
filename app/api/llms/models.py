import uuid
from typing import Optional, List
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
        

class Message(BaseModel):
    role: str
    text: str

class Conversation(BaseModel):
    id: str = Field(default_factory=uuid.uuid4, alias="_id")
    user: str
    messages: List[Message] = Field(default_factory=list)
    type: str = Field(default="chat")
    processing_slug: str = None
    record_id: str = None
    resource_id: str = None
    
    class Config:
        populate_by_name = True
        
class ConversationUpdate(BaseModel):
    messages: Optional[List[Message]] = None
    
    class Config:
        populate_by_name = True