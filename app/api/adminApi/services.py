from app.utils import DatabaseHandler
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()

def autoComplete(body):
    if 'filesIds' not in body:
        body['filesIds'] = []
    if 'post_type' not in body:
        from app.api.system.services import get_default_cataloging_type
        postType, status = get_default_cataloging_type()
        if status != 200:
            return jsonify({'msg': _('Unable to get the default cataloging type')}), 500
        body['post_type'] = postType
    if 'status' not in body:
        body['status'] = 'published'
    if 'parent' not in body:
        body['parent'] = None
    if 'parents' not in body:
        body['parents'] = []
    if 'updateCache' not in body:
        body['updateCache'] = False
        
    return body

def create(body, user, files):
    body = autoComplete(body)
    from app.api.resources.services import create as create_resource
    return create_resource(body, user, files, body['updateCache'])

def update(id, body, user, files):
    body = autoComplete(body)
    from app.api.resources.services import update_by_id as update_resource
    return update_resource(id, body, user, files, body['updateCache'])

def get_id(body, user):
    resource = None
    resource = mongodb.get_record('resources', body, {'_id': 1, 'post_type': 1, 'metadata': 1, 'filesObj': 1, 'parent': 1, 'parents': 1})
    if resource is None:
        return {'msg': _('Resource not found')}, 404

    return {'id': str(resource['_id']), 'post_type': resource['post_type'], 'metadata': resource['metadata'], 'filesObj': resource['filesObj'], 'parent': resource['parent'], 'parents': resource['parents']}, 200

def get_opts_id(body, user):
    options = mongodb.get_record('options', {'term': body['term']}, {'_id': 1})

    if options is None:
        return {'msg': _('Option not found')}, 404
    

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