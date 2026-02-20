from app.utils import CacheHandler
from app.utils import DatabaseHandler
from app.api.views.models import View, ViewUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from bson.objectid import ObjectId
from flask_babel import _
from app.api.records.services import create as create_record
from app.api.records.services import delete_parent
import os
import base64

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

def update_cache():
    get.invalidate_all()
    get_view_info.invalidate_all()
    get_all.invalidate_all()


def _is_image_upload(file):
    mime = getattr(file, 'mimetype', None)
    if mime and 'image' in mime:
        return True

    filename = getattr(file, 'filename', '') or ''
    ext = os.path.splitext(filename)[1].lower()
    return ext in ['.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff', '.heic', '.bmp', '.webp']


def _process_uploaded_record(record_id):
    record = mongodb.get_record('records', {'_id': ObjectId(record_id)}, fields={'mime': 1, 'filepath': 1, 'processing.fileProcessing.type': 1})
    if not record:
        return {'msg': _('Record does not exist')}, 404

    if 'mime' not in record or not record['mime'] or 'image' not in record['mime']:
        return {'msg': _('Only image files are allowed for views')}, 400

    from app.plugins.filesProcessing import process_file, ExtendedPluginClass, plugin_info
    instance = ExtendedPluginClass('filesProcessing', '', isTask=True, **plugin_info)
    process_file({'_id': ObjectId(record_id), 'mime': record['mime'], 'filepath': record['filepath']}, instance)

    processed = mongodb.get_record('records', {'_id': ObjectId(record_id)}, fields={'processing.fileProcessing.type': 1})
    processed_type = processed.get('processing', {}).get('fileProcessing', {}).get('type') if processed else None
    if processed_type != 'image':
        return {'msg': _('File processing failed for image')}, 500

    return None


def _get_thumbnail_from_files_obj(files_obj):
    if not files_obj or not isinstance(files_obj, list):
        return None

    thumbnail_file = next((f for f in files_obj if isinstance(f, dict) and f.get('tag') == 'thumbnail'), None)
    if not thumbnail_file:
        thumbnail_file = next((f for f in files_obj if isinstance(f, dict) and f.get('id')), None)

    if not thumbnail_file or 'id' not in thumbnail_file:
        return None

    try:
        record = mongodb.get_record(
            'records',
            {'_id': ObjectId(thumbnail_file['id'])},
            fields={'processing.fileProcessing.path': 1, 'processing.fileProcessing.type': 1}
        )
    except Exception:
        return None

    if not record:
        return None

    file_processing = record.get('processing', {}).get('fileProcessing', {})
    if file_processing.get('type') != 'image' or 'path' not in file_processing:
        return None

    image_path = os.path.join(WEB_FILES_PATH, file_processing['path'] + '_medium.jpg')
    if not os.path.exists(image_path):
        return None

    with open(image_path, 'rb') as image_file:
        return 'data:image/jpeg;base64,' + base64.b64encode(image_file.read()).decode('utf-8')


def _map_view_thumbnail(view):
    view['thumbnail'] = _get_thumbnail_from_files_obj(view.get('filesObj'))
    if 'filesObj' in view:
        view.pop('filesObj')
    return view

@cacheHandler.cache.cache()
def get(id, user):
    view = mongodb.get_record('views', {'_id': ObjectId(id)})

    if not view:
        return {'msg': _('View not found')}, 404
    
    _map_view_thumbnail(view)
    view.pop('_id')

    return view, 200

@cacheHandler.cache.cache()
def get_view_info(view_slug):
    view = mongodb.get_record('views', {'slug': view_slug}, fields={'name': 1, 'description': 1, 'parent': 1, 'root': 1, 'visible': 1})

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

    filter_condition = {'parent.post_type': {'$in': [p['slug'] for p in types]}, 'status': {'$ne': 'deleted'}}
    if view['parent'] != '':
        filter_condition = {
            '$or': [
                {'parents.id': view['parent']},
                {'parent.id': view['parent']}
            ]
        }

    records_count = mongodb.count(
            'records', filter_condition)
    
    distinct_types = ['video', 'audio', 'document', 'image', 'database']
    records_types = []
    
    for file_type in distinct_types:
        type_filter = {**filter_condition, 'processing.fileProcessing.type': file_type}
        count = mongodb.count('records', type_filter)
        records_types.append({'_id': file_type, 'count': count})

    records_types.sort(key=lambda x: x['count'], reverse=True)
    
    
    view['files'] = {
        'total': records_count,
        'data': records_types
    }
    
    return view, 200

def update(id, body, user, files):
    try:
        if files and len(files) > 1:
            return {'msg': _('A view can only have one file')}, 400

        if files and len(files) == 1 and not _is_image_upload(files[0]):
            return {'msg': _('Only image files are allowed for views')}, 400

        current_view = mongodb.get_record('views', {'_id': ObjectId(id)}, fields={'filesObj': 1})
        if not current_view:
            return {'msg': _('View not found')}, 404

        update_body = {**body}

        if files and len(files) == 1:
            if 'filesObj' in current_view and current_view['filesObj']:
                for file_obj in current_view['filesObj']:
                    if 'id' in file_obj:
                        resp = delete_parent(id, file_obj['id'], user)
                        if isinstance(resp, tuple) and len(resp) == 2 and resp[1] != 200:
                            return resp

            records = create_record(
                id,
                user,
                files,
                filesTags=[{'filetag': 'thumbnail'}],
                parent_data={'post_type': 'view', 'parents': []}
            )

            process_error = _process_uploaded_record(records[0]['id'])
            if process_error:
                delete_parent(id, records[0]['id'], user)
                return process_error

            update_body['filesObj'] = records[:1]

        view = ViewUpdate(**update_body)
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

    resp = []
    for view in views:
        _map_view_thumbnail(view)
        resp.append({
            'name': view['name'],
            'id': str(view['_id']),
            'description': view['description'],
            'slug': view['slug'],
            'thumbnail': view.get('thumbnail')
        })

    return resp, 200

def create(body, user, files):
    try:
        if files and len(files) > 1:
            return {'msg': _('A view can only have one file')}, 400

        if files and len(files) == 1 and not _is_image_upload(files[0]):
            return {'msg': _('Only image files are allowed for views')}, 400

        if 'filesObj' not in body:
            body['filesObj'] = []

        view = View(**body)
        view_created = mongodb.insert_record('views', view)

        if files and len(files) == 1:
            records = create_record(
                str(view_created.inserted_id),
                user,
                files,
                filesTags=[{'filetag': 'thumbnail'}],
                parent_data={'post_type': 'view', 'parents': []}
            )

            process_error = _process_uploaded_record(records[0]['id'])
            if process_error:
                delete_parent(str(view_created.inserted_id), records[0]['id'], user)
                mongodb.delete_record('views', {'_id': ObjectId(view_created.inserted_id)})
                return process_error

            update = ViewUpdate(**{'filesObj': records[:1]})
            mongodb.update_record('views', {'_id': ObjectId(view_created.inserted_id)}, update)

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
        view = mongodb.get_record('views', {'_id': ObjectId(id)}, fields={'filesObj': 1})
        if view and 'filesObj' in view and view['filesObj']:
            for file_obj in view['filesObj']:
                if 'id' in file_obj:
                    resp = delete_parent(id, file_obj['id'], user)
                    if isinstance(resp, tuple) and len(resp) == 2 and resp[1] != 200:
                        return resp

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