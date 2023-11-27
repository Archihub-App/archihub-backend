from app.api.resources.services import create as create_resource
from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()

def create(body, user):
    return create_resource(body, user, body['files'])

def get_id(body, user):
    if 'title' in body:
        resource = mongodb.get_record('resources', {'metadata.firstLevel.title': body['title']}, {'_id': 1, 'post_type': 1})
    elif 'ident' in body:
        resource = mongodb.get_record('resources', {'ident': body['ident']}, {'_id': 1, 'post_type': 1})

    if resource is None:
        return {'msg': 'No existe ese recurso'}, 400

    return {'id': str(resource['_id']), 'post_type': resource['post_type']}, 200

def get_opts_id(body, user):
    options = mongodb.get_record('options', {'term': body['term']}, {'_id': 1})

    if options is None:
        return {'msg': 'No existe esa opción'}, 400
    

    return {'id': str(options['_id'])}, 200