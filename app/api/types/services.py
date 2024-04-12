from flask import jsonify
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.types.models import PostType
from app.api.types.models import PostTypeUpdate
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.system.services import get_access_rights_id
from app.utils.functions import verify_role_exists
from app.utils.functions import clear_cache

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

# Funcion para parsear el resultado de una consulta a la base de datos


def parse_result(result):
    return json.loads(json_util.dumps(result))


def update_cache():
    get_all.invalidate_all()
    get_by_slug.invalidate_all()
    get_metadata.invalidate_all()
    get_types_info.invalidate_all()
    get_count.invalidate_all()
    clear_cache()

# Nuevo servicio para obtener todos los tipos de


@cacheHandler.cache.cache()
def get_all():
    try:
        # Obtener todos los tipos de post en orden alfabetico ascendente por el campo name
        post_types = mongodb.get_all_records('post_types', {}, [('name', 1)])
        # Quitar todos los campos menos el nombre y la descripción
        post_types = [{'name': post_type['name'], 'description': post_type['description'],
                       'slug': post_type['slug']} for post_type in post_types]
        # Retornar post_types
        return post_types, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un tipo de post


def create(body, user):
    try:
        # Crear instancia de PostType con el body del request
        post_type = PostType(**body)
        # Insertar el tipo de post en la base de datos
        new_post_type = mongodb.insert_record('post_types', post_type)
        # Registrar el log
        register_log(user, log_actions['type_create'], {'post_type': {
            'name': post_type.name,
            'slug': post_type.slug,
        }})
        # Limpiar la cache
        update_cache()
        # Retornar el resultado
        return {'msg': 'Tipo de post creado exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para obtener un tipo de post por su slug


@cacheHandler.cache.cache()
def get_by_slug(slug):
    try:
        # Buscar el tipo de post en la base de datos
        post_type = mongodb.get_record('post_types', {'slug': slug})
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}
        # quitamos el id del tipo de post
        post_type.pop('_id')
        # Parsear el resultado
        post_type = parse_result(post_type)
        # Obtener los padres del tipo de post
        parents = get_parents(post_type)
        # si es jerarquico, agregar campo a los padres
        if post_type['hierarchical']:
            parents = [{'name': post_type['name'], 'slug': post_type['slug'],
                        'icon': post_type['icon'], 'direct': True}] + parents
        # Agregar los padres al tipo de post
        post_type['parentsTypes'] = parents
        # Si el campo metadata es un string y es distinto a '', recuperar el formulario con ese slug
        if type(post_type['metadata']) == str and post_type['metadata'] != '':
            post_type['metadata'] = get_form_by_slug(post_type['metadata'])
            # dejar solo los campos name y slug del formulario
            post_type['metadata'] = {'name': post_type['metadata']['name'],
                                     'fields': post_type['metadata']['fields'], 'slug': post_type['metadata']['slug']}
        else:
            post_type['metadata'] = None

        # Retornar el resultado
        return post_type
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para actualizar un tipo de post


def update_by_slug(slug, body, user):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    try:
        if 'editRoles' in body:
            body['editRoles'] = verify_role_exists(body['editRoles'])

        if 'viewRoles' in body:
            body['viewRoles'] = verify_role_exists(body['viewRoles'])
        # crear instancia de PostTypeUpdate con el body del request
        post_type_update = PostTypeUpdate(**body)
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}, 404
        # Actualizar el tipo de post
        mongodb.update_record('post_types', {'slug': slug}, post_type_update)
        # Registrar el log
        register_log(user, log_actions['type_update'], {'post_type': body})
        # Limpiar la cache
        update_cache()
        # Retornar el resultado
        return {'msg': 'Tipo de post actualizado exitosamente.'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para eliminar un tipo de post


def delete_by_slug(slug, user):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Eliminar el tipo de post
    mongodb.delete_record('post_types', {'slug': slug})
    # Eliminar todos los recursos del tipo de post
    mongodb.delete_records('resources', {'post_type': slug})
    # Registrar el log
    register_log(user, log_actions['type_delete'], {'post_type': {
        'name': post_type['name'],
        'slug': post_type['slug'],
    }})
    # Limpiar la cache
    update_cache()
    # Retornar el resultado
    return {'msg': 'Tipo de post eliminado exitosamente'}, 204

# Funcion que devuelve recursivamente los padres de un tipo de post


def get_parents(post_type, first=True):
    # Si el tipo de post no tiene padre, retornar una lista vacia
    if post_type['parentType'] == '':
        return []
    # Buscar el padre del tipo de post
    parent = mongodb.get_record(
        'post_types', {'slug': post_type['parentType']})
    # Si el padre no existe, retornar una lista vacia
    if not parent and not parent['hierarchical']:
        return []
    # Retornar el padre y los padres del padre
    return [{
        'name': parent['name'],
        'slug': parent['slug'],
        'icon': parent['icon'],
        'direct': True if first else False
    }] + get_parents(parent, False)

# Funcion para agregar al contador de recursos de un tipo de post


def add_resource(post_type_slug, increment=1):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': post_type_slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Incrementar el contador de recursos del tipo de post
    mongodb.increment_record(
        'post_types', {'slug': post_type_slug}, 'resourcesCount', increment)


# Funcion para devolver si el tipo de post es jerarquico y si tiene padres
def is_hierarchical(post_type_slug):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': post_type_slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404

    # Retornar el resultado
    return (post_type['hierarchical'], post_type['parentType'] != '')

# Funcion para devolver el icono de un tipo de post


@cacheHandler.cache.cache()
def get_icon(post_type_slug):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': post_type_slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Retornar el resultado
    return post_type['icon']

# Funcion para devolver los campos del metadato de un tipo de post


@cacheHandler.cache.cache()
def get_metadata(post_type_slug):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': post_type_slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Si el campo metadata es un string y es distinto a '', recuperar el formulario con ese slug
    if type(post_type['metadata']) == str and post_type['metadata'] != '':
        post_type['metadata'] = get_form_by_slug(post_type['metadata'])
    else:
        post_type['metadata'] = None

    # Retornar el resultado
    return post_type['metadata']


@cacheHandler.cache.cache()
def get_form_by_slug(slug):
    try:
        # Buscar el formulario en la base de datos
        form = mongodb.get_record('forms', {'slug': slug})
        # Si el formulario no existe, retornar error
        if not form:
            return {'msg': 'Formulario no existe'}

        # Agregamos un nuevo campo al inicio del arreglo de fields, que es el campo de accessRights
        form['fields'].insert(0, {
            'name': 'accessRights',
            'label': 'Derechos de acceso',
            'required': True,
            'destiny': 'accessRights',
            'list': get_access_rights_id(),
            'type': 'select'
        })
        # quitamos el id del formulario
        form.pop('_id')
        # Parsear el resultado
        form = parse_result(form)
        # Retornar el resultado
        return form
    except Exception as e:
        return {'msg': str(e)}, 500


@cacheHandler.cache.cache()
def get_types_info():
    try:
        # Obtener todos los tipos de post en orden alfabetico ascendente por el campo name
        post_types = list(mongodb.get_all_records(
            'post_types',  {'$or': [{'viewRoles': {'$exists': False}}, {'viewRoles': {'$eq': []}}]}, [('name', 1)]))

        # Quitar todos los campos menos el nombre y la descripción
        post_types = [{'name': post_type['name'], 'slug': post_type['slug']}
                      for post_type in post_types]
        
        for p in post_types:
            p['count'] = get_count(p['slug'])

        # order post_types by count
        post_types = sorted(
            post_types, key=lambda k: k['count'], reverse=False)
        # get last item of post_types
        last = post_types[-1]

        for p in post_types:
            p['percent'] = round((p['count'] / last['count']) * 100)

        # records count
        records_count = mongodb.count(
            'records', {'viewRoles': {'$exists': True, '$ne': []}})

        records_types = list(mongodb.aggregate('records', [
            {'$group': {'_id': '$processing.fileProcessing.type', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]))

        resp = {
            'types': post_types,
            'files': {
                'total': records_count,
                'data': records_types
            }
        }
        # Retornar post_types
        return jsonify(resp), 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500


@cacheHandler.cache.cache()
def get_count(type):
    try:
        # Obtener todos los tipos de post en orden alfabetico ascendente por el campo name
        count = mongodb.count('resources', {'post_type': type})

        # Retornar post_types
        return count
    except Exception as e:
        raise Exception(str(e))
