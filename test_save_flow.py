import json
import asyncio
from archihub.plugins.flowsManager.__init__ import FlowDataModel

flow_payload = {
    "nodes": [
        {"id": "1", "type": "some_node", "settings": {"foo": "bar"}}
    ]
}

model = FlowDataModel(
    name="Test",
    flow=flow_payload
)
print("Model Dump:", json.dumps(model.model_dump(), default=str))

