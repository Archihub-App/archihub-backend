import os
from pymongo import MongoClient
import json

client = MongoClient("mongodb://admin:FxxS7XYDgy@localhost:27017/")
db = client["archihub-prod"]
flows = list(db["flows"].find().sort("_id", -1).limit(1))
if flows:
    flow = flows[0]
    print(json.dumps(flow.get("flow", {}), default=str))
else:
    print("No flows found")
