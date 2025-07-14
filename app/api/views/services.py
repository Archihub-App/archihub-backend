from app.utils import CacheHandler
from app.utils import DatabaseHandler
from app.api.views.models import View, ViewUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from bson.objectid import ObjectId
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def update_cache():
    get.invalidate_all()
    get_view_info.invalidate_all()
    get_all.invalidate_all()

@cacheHandler.cache.cache()
def get(id, user):
    view = mongodb.get_record('views', {'_id': ObjectId(id)})

    if not view:
        return {'msg': _('View not found')}, 404
    
    view.pop('_id')

    return view, 200

@cacheHandler.cache.cache()
def get_view_info(view_slug):
    view = mongodb.get_record('views', {'slug': view_slug})

    forms = []
    fields= []
    for v in view['visible']:
        from app.api.types.services import get_by_slug
        pt = get_by_slug(v)
        form = pt['metadata']['slug']
        if form not in [f['slug'] for f in forms]:
            forms.append({
                'slug': form,
                'name': pt['metadata']['name']
            })
            fields.append(pt['metadata']['fields'])

    view['forms'] = {
        'forms': forms,
        'fields': fields
    }

    if not view:
        return {'msg': _('View not found')}, 404
    

    types = []
    tree_types = []

    for v in view['visible']:
        from app.api.types.services import get_by_slug
        from app.api.types.services import get_parents
        pt = get_by_slug(v)
        pt_parents = get_parents(pt)
        if pt_parents:
            for p in pt_parents:
                if p['slug'] not in [t['slug'] for t in tree_types]:
                    tree_types.append(p)
                    
        types.append({
            'slug': v,
            'description': pt['description'],
            'name': pt['name'],
            'icon': pt['icon']
        })

    view.pop('_id')
    view.pop('visible')
    view['types'] = types
    view['tree_types'] = tree_types
    from app.api.types.services import get_icon
    view['icon'] = get_icon(view['root'])

    filter_condition = {'parent.post_type': {'$in': [p['slug'] for p in types]}}
    if view['parent'] != '':
        filter_condition = {
            '$or': [
                {'parents.id': view['parent']},
                {'parent.id': view['parent']}
            ]
        }

    records_count = mongodb.count(
            'records', filter_condition)

    records_types = list(mongodb.aggregate('records', [
            {'$match': filter_condition},
            {'$group': {'_id': '$processing.fileProcessing.type', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]))
    
    view['files'] = {
        'total': records_count,
        'data': records_types
    }
    
    return view, 200

def update(id, body, user):
    try:
        view = ViewUpdate(**body)
        view_updated = mongodb.update_record('views', {'_id': ObjectId(id)}, view)
        update_cache()

        log = {
            'data': view_updated.raw_result
        }

        register_log(user, log_actions['view_update'], log)

        return {'msg': _('View updated successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache()
def get_all():
    views = mongodb.get_all_records('views', {}, [('name', 1), ('description', 1), ('slug', 1)])

    resp = [{ 'name': view['name'], 'id': str(view['_id']), 'description': view['description'], 'slug': view['slug'] } for view in views]

    return resp, 200

def create(body, user):
    try:
        view = View(**body)
        view_created = mongodb.insert_record('views', view)

        update_cache()

        log = {
            'data': view_created.inserted_id
        }

        register_log(user, log_actions['view_create'], log)

        return {'msg': _('View created successfully')}, 201
    except Exception as e:
        return {'msg': str(e)}, 500
    
def delete(id, user):
    try:
        view_deleted = mongodb.delete_record('views', {'_id': ObjectId(id)})

        log = {
            'data': view_deleted.raw_result
        }
        
        
        get_all.invalidate_all()

        register_log(user, log_actions['view_delete'], log)
        get_all.invalidate_all()

        return {'msg': _('View deleted successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500