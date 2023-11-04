from flask import jsonify, request
from app.utils import IndexHandler

index_handler = IndexHandler.IndexHandler()

def get_resources_by_filters(body, user):
    print(body)

    return jsonify({'msg': 'ok'}), 200