from fastapi.testclient import TestClient
from fastapi import FastAPI
from archihub.plugins.flowsManager.__init__ import build, FlowDataModel
import json

app = FastAPI()
plugin = build()
app.include_router(plugin.router)

client = TestClient(app)
payload = {
    "name": "Test Flow",
    "flow": {
        "nodes": [
            {
                "id": "1",
                "type": "string",
                "settings": {"system_prompt": "hello"}
            }
        ],
        "edges": []
    }
}
print("Original payload:", json.dumps(payload))
# We cannot run the API easily because it requires _mongo() and Depends(require_roles).
# Let's just test FlowDataModel
model = FlowDataModel(**payload)
print("Pydantic dump:", json.dumps(model.model_dump(), default=str))
