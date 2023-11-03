from app.api.resources.services import create as create_resource
from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()

def create(body, user):
    return create_resource(body, user, None)

def get_id(body, user):
    resource = mongodb.get_record('resources', {'metadata.firstLevel.title': body['title']}, {'_id': 1, 'post_type': 1})
    return {'id': str(resource['_id']), 'post_type': resource['post_type']}, 200