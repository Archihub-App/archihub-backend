from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()

def create(body, user, files):
    from app.api.resources.services import create as create_resource
    return create_resource(body, user, files)

def update(id, body, user):
    from app.api.resources.services import update_by_id as update_resource
    return update_resource(id, body, user, body['files'])

def get_id(body, user):
    resource = None
    resource = mongodb.get_record('resources', body, {'_id': 1, 'post_type': 1})

    if resource is None:
        return {'msg': 'No existe ese recurso'}, 400

    return {'id': str(resource['_id']), 'post_type': resource['post_type']}, 200

def get_opts_id(body, user):
    options = mongodb.get_record('options', {'term': body['term']}, {'_id': 1})

    if options is None:
        return {'msg': 'No existe esa opción'}, 400
    

    return {'id': str(options['_id'])}, 200

def create_type(body, user):
    from app.api.types.services import create
    return create(body, user)

def get_type(slug, user):
    from app.api.types.services import get_by_slug
    return get_by_slug(slug)

def update_type(slug, body, user):
    from app.api.types.services import update_by_slug
    return update_by_slug(slug, body, user)