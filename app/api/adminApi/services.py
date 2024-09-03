from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()

def create(body, user, files):
    from app.api.resources.services import create as create_resource
    return create_resource(body, user, files)

def update(id, body, user, files):
    from app.api.resources.services import update_by_id as update_resource
    return update_resource(id, body, user, files)

def get_id(body, user):
    resource = None
    resource = mongodb.get_record('resources', body, {'_id': 1, 'post_type': 1, 'metadata': 1, 'filesObj': 1, 'parent': 1, 'parents': 1})

    if resource is None:
        return {'msg': 'No existe ese recurso'}, 400

    return {'id': str(resource['_id']), 'post_type': resource['post_type'], 'metadata': resource['metadata'], 'filesObj': resource['filesObj']}, 200

def get_opts_id(body, user):
    options = mongodb.get_record('options', {'term': body['term']}, {'_id': 1})

    if options is None:
        return {'msg': 'No existe esa opción'}, 400
    

    return {'id': str(options['_id'])}, 200

def create_type(body, user):
    from app.api.types.services import create
    return create(body, user)

def get_type(slug, user):
    try:
        from app.api.types.services import get_by_slug
        return get_by_slug(slug)
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

def update_type(body, user):
    from app.api.types.services import update_by_slug
    return update_by_slug(body['slug'], body, user)