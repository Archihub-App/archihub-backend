from bson.objectid import ObjectId
import pandas as pd
import os
import uuid
from app.utils import DatabaseHandler
mongodb = DatabaseHandler.DatabaseHandler()

def generateResourceInventory(body, user=None, path=None, filename=None):
    def clean_string(input_string):
        if input_string is None:
            return ''
        cleaned_string = ''.join(char for char in input_string if ord(char) > 31 or ord(char) in (9, 10, 13))
        return cleaned_string

    filters = {
        'post_type': body['post_type']
    }
    
    from app.api.types.services import get_by_slug
    type = get_by_slug(body['post_type'])
    
    type_metadata = type['metadata']
    type_metadata_name = type['name']

    resources_df = []
    records_df = []

    if 'parent' in body:
        if body['parent']:
            filters = {'$or': [{'parents.id': body['parent'], 'post_type': body['post_type']}, {'_id': ObjectId(body['parent'])}]}

            if 'status' not in body:
                for o in filters['$or']:
                    o['status'] = 'published'
            elif body['status'] == 'draft':
                for o in filters['$or']:
                    o['status'] = 'draft'

                from app.api.users.services import has_role
                if user:
                    if not has_role(user, 'publisher') or not has_role(user, 'admin'):
                        for o in filters['$or']:
                            o['createdBy'] = user

    if 'status' not in body:
        filters['status'] = 'published'
    elif body['status'] == 'draft':
        filters['status'] = 'draft'

        from app.api.users.services import has_role
        if user:
            if not has_role(user, 'publisher') or not has_role(user, 'admin'):
                filters['createdBy'] = user
    elif body['status'] == 'published':
        filters['status'] = 'published'

    # buscamos los recursos con los filtros especificados
    resources = list(mongodb.get_all_records('resources', filters))

    # si no hay recursos, retornamos un error
    if len(resources) == 0:
        raise Exception('No se encontraron recursos con los filtros especificados')
    
    obj = {}

    obj['Tipo de contenido'] = 'post_type'
    obj['id'] = 'id'
    obj['ident'] = 'ident'

    type_metadata['fields'] = [f for f in type_metadata['fields'] if f['type'] != 'file']

    for f in type_metadata['fields']:
        obj[f['label']] = f['destiny']

    resources_df.append(obj)


    # si hay recursos, iteramos
    for r in resources:
        obj = {}
        
        obj['id'] = str(r['_id'])
        obj['ident'] = r['ident']
        obj['Tipo de contenido'] = r['post_type']

        from app.api.resources.services import get_value_by_path
        for f in type_metadata['fields']:
            if f['type'] == 'text' or f['type'] == 'text-area':
                obj[f['label']] = clean_string(get_value_by_path(r, f['destiny']))
            elif f['type'] == 'select':
                obj[f['label']] = clean_string(get_value_by_path(r, f['destiny']))
            elif f['type'] == 'simple-date':
                date = get_value_by_path(r, f['destiny'])
                if date:
                    obj[f['label']] = date.strftime('%Y-%m-%d')
                else:
                    obj[f['label']] = ''
            elif f['type'] == 'number':
                obj[f['label']] = get_value_by_path(r, f['destiny'])

        resources_df.append(obj)

        # r_ = list(mongodb.get_all_records('records', {'parent.id': str(r['_id'])}, fields={'name': 1, 'displayName': 1}))

        # files = [record['name'] for record in r_]
        # obj['files'] = ', '.join(files)

        # files_ids = [str(record['_id']) for record in r_]
        # obj['files_ids'] = ', '.join(files_ids)

    resources = [str(resource['_id']) for resource in resources]
    
    # records_filters = {
    #     'parent.id': {'$in': resources},
    # }

    # records = list(mongodb.get_all_records('records', records_filters, fields={
    #     '_id': 1, 'mime': 1, 'filepath': 1, 'hash': 1, 'size': 1}))
    
    # for r in records:
    #     obj = {
    #         'id': str(r['_id']),
    #         'mime': r['mime'],
    #         'filepath': r['filepath'],
    #         'hash': r['hash'],
    #         'size': r['size']
    #     }

    #     records_df.append(obj)

    folder_path = path
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    file_id = str(uuid.uuid4()) if filename is None else filename

    if not os.path.exists(folder_path + '/' + file_id + '.xlsx'):
        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(resources_df)
            df.to_excel(writer, sheet_name='Recursos', index=False)
            # df = pd.DataFrame(records_df)
            # df.to_excel(writer, sheet_name='Archivos', index=False)
    elif os.path.getsize(folder_path + '/' + file_id + '.xlsx') == 0:
        raise Exception('El archivo ya existe y se está generando')
    
    return folder_path + '/' + file_id + '.xlsx'