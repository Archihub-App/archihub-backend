from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.forms.models import Form
from app.api.forms.models import FormUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.system.services import update_resources_schema
from app.api.system.services import get_access_rights_id
from app.api.types.services import get_by_slug as get_type_by_slug
from app.utils.functions import get_access_rights

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    get_all.invalidate_all()
    get_by_slug.invalidate_all()
    get_type_by_slug.invalidate_all()

# Nuevo servicio para obtener todos los estándares de metadatos
@cacheHandler.cache.cache()
def get_all():
    try:
        # Obtener todos los estándares de metadatos
        forms = mongodb.get_all_records('forms', {}, [('name', 1)])
        # Quitar todos los campos menos el nombre y la descripción
        forms = [{ 'name': form['name'], 'description': form['description'], 'slug': form['slug']} for form in forms]
        # Retornar forms
        return forms, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un estándar de metadatos
def create(body, user):
    # Crear instancia de Form con el body del request
    try:
        # si el slug no está definido, crearlo
        if 'slug' not in body or body['slug'] == '':
            body['slug'] = body['name'].lower().replace(' ', '-')
            # quitamos los caracteres especiales y las tildes pero dejamos los guiones
            body['slug'] = ''.join(e for e in body['slug'] if e.isalnum() or e == '-')
            # quitamos los guiones al inicio y al final
            body['slug'] = body['slug'].strip('-')
            # quitamos los guiones repetidos
            body['slug'] = body['slug'].replace('--', '-')

            # llamamos al servicio para verificar si el slug ya existe
            slug_exists = get_by_slug(body['slug'])
            # Mientras el slug exista, agregar un número al final
            index = 1
            while 'msg' not in slug_exists:
                body['slug'] = body['slug'] + '-' + str(index)
                slug_exists = get_by_slug(body['slug'])
                index += 1
                
        else:
            slug_exists = get_by_slug(body['slug'])
            index = 1
            while 'msg' not in slug_exists:
                body['slug'] = body['slug'] + '-' + str(index)
                slug_exists = get_by_slug(body['slug'])
                index += 1
            
        validate_form(body)
        # se verifica el arbol completo de metadatos de la herramienta
        update_main_schema(new_form=body)

        form = Form(**body)
        # Insertar el estándar de metadatos en la base de datos
        new_form = mongodb.insert_record('forms', form)
        # Registrar el log
        register_log(user, log_actions['form_create'], {'form': {
            'name': form.name,
            'slug': form.slug,
        }})
        # Limpiar la cache
        get_by_slug.invalidate_all()
        get_all.invalidate_all()
        # Retornar el resultado
        return {'msg': 'Formulario creado exitosamente'}, 201
    except Exception as e:
            print(str(e))
            return {'msg': str(e)}, 500

# Nuevo servicio para devolver un formulario por su slug
@cacheHandler.cache.cache()
def get_by_slug(slug):
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

# Nuevo servicio para actualizar un formulario
def update_by_slug(slug, body, user):
    # Buscar el formulario en la base de datos
    try:
        validate_form(body)
        # se verifica el arbol completo de metadatos de la herramienta
        update_main_schema(updated_form=body)

        form = mongodb.get_record('forms', {'slug': slug})
        # Si el formulario no existe, retornar error
        if not form:
            return {'msg': 'Formulario no existe'}, 404
        # Crear instancia de FormUpdate con el body del request
        form_update = FormUpdate(**body)
        # Actualizar el formulario en la base de datos
        mongodb.update_record('forms', {'slug': slug}, form_update)
        # Registrar el log
        register_log(user, log_actions['form_update'], {'form': body})
        # Limpiar la cache
        update_cache()
        # Retornar el resultado
        return {'msg': 'Formulario actualizado exitosamente. Si se agregaron nuevos campos o se cambió el tipo de alguno de los campos existentes, es importante volver a generar el índice desde la opción en los ajustes del sistema.'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un formulario
def delete_by_slug(slug, user):
    # Buscar el formulario en la base de datos
    try:
        form = mongodb.get_record('forms', {'slug': slug})
        # Si el formulario no existe, retornar error
        if not form:
            return {'msg': 'Formulario no existe'}, 404
        # verificar que no existan tipos de post que usen el formulario
        post_types = list(mongodb.get_all_records('post_types', {'metadata': slug}))

        if(len(post_types) > 0):
            return {'msg': 'No se puede eliminar el formulario porque existen tipos de post que lo usan'}, 400
        
        # Eliminar el formulario de la base de datos
        mongodb.delete_record('forms', {'slug': slug})
        # Registrar el log
        register_log(user, log_actions['form_delete'], {'form': {
            'name': form['name'],
            'slug': form['slug']
        }})
        # Limpiar la cache
        get_all.invalidate_all()
        get_by_slug.invalidate_all()
        # Retornar el resultado
        return {'msg': 'Formulario eliminado exitosamente'}, 204
    except Exception as e:
        return {'msg': str(e)}, 500
    

# Funcion que valida que el formulario tenga todos los campos requeridos
def validate_form(form):
    # verificar que no tenga dos fields de tipo file
    files = 0
    # verificar que no tenga un field con destiny igual a ident
    for field in form['fields']:
        if 'destiny' in field:
            if field['destiny'] == 'ident':
                raise Exception("Error: el formulario no puede tener un campo con destino igual a ident")
            
            if not field['destiny'].startswith('metadata') and not field['destiny'] == 'file' and not field['type'] == 'separator':
                raise Exception("Error: el formulario no puede tener un campo con destino que no inicie con metadata")
            
        if field['type'] == 'file':
            files += 1

        if 'accessRights' in field:
            if field['accessRights']:
                options = get_access_rights()
                options = [o['id'] for o in options['options']]
                for f in field['accessRights']:
                    if f not in options:
                        raise Exception("Error: el campo accessRights tiene un valor que no es válido")
            
    if files > 1:
        raise Exception("Error: el formulario no puede tener dos campos de tipo archivo")
            
    
# Funcion que itera entre todos los formularios y devuelve la estructura combinada de todos
def update_main_schema(new_form = None, updated_form = None):
    try:
        # diccionario que contiene la estructura de todos los formularios
        resp = {}
        # arreglo que contiene los tipos de campos que son iguales
        same_types = [
            'select',
            'select-multiple2',
        ]
        # Obtener todos los formularios
        filters = {}
        if updated_form:
            filters['slug'] = {'$ne': updated_form['slug']}
        
        forms = mongodb.get_all_records('forms', filters, [('name', 1)])
        # Iterar entre todos los formularios
        for form in forms:
            # Iterar el campo fields del formulario
            for field in form['fields']:
                # Si el campo no tiene el atributo 'form', se agrega el slug del formulario
                tipo = field['type']
                if 'destiny' in field:
                    # el destino del campo es de tipo llave.llave (ej: 'metadata.title'), agregamos el campo al diccionario resp con el tipo del campo
                    if(field['destiny'] in resp):
                        if(resp[field['destiny']] != tipo):
                            if(tipo not in same_types and resp[field['destiny']] not in same_types):
                                # si el tipo del campo no es igual al tipo del campo que ya existe en el diccionario, se lanza una excepcion
                                raise Exception("Error: el campo " + field['destiny'] + " tiene dos tipos diferentes")
                    else:
                        resp[field['destiny']] = tipo

        # si se agrego un nuevo formulario, se itera entre los campos del formulario y se agregan al diccionario resp
        if new_form:
            for field in new_form['fields']:
                tipo = field['type']
                if 'destiny' in field:
                    if(field['destiny'] in resp):
                        if(resp[field['destiny']] != tipo):
                            if(tipo not in same_types and resp[field['destiny']] not in same_types):
                                raise Exception("Error: el campo " + field['destiny'] + " tiene dos tipos diferentes")
                    else:
                        resp[field['destiny']] = tipo

        if updated_form:
            for field in updated_form['fields']:
                tipo = field['type']
                if 'destiny' in field:
                    if(field['destiny'] in resp):
                        if(resp[field['destiny']] != tipo):
                            if(tipo not in same_types and resp[field['destiny']] not in same_types):
                                raise Exception("Error: el campo " + field['destiny'] + " tiene dos tipos diferentes")
                    else:
                        resp[field['destiny']] = tipo

        # se itera entre todos los campos del diccionario resp y se transforman las llaves en diccionarios. Por ejemplo, si la llave es 'metadata.title', se transforma en {'metadata': {'title': resp['metadata.title']}}, y se agrega al diccionario final 'final_resp'
        final_resp = {}
        for key in resp:
            # se obtiene el arreglo de llaves
            keys = key.split('.')
            # se obtiene el tipo del campo
            tipo = resp[key]
            if tipo in same_types:
                tipo = 'select'
            # se obtiene el valor del campo
            value = {
                'type': tipo,
            }
            # se itera entre las llaves del arreglo
            for i in range(len(keys) - 1, -1, -1):
                # se crea un diccionario con la llave actual y el valor del campo
                value = {
                    keys[i]: value
                }
            # se agrega el diccionario haciendo un merge con el diccionario final
            merge_dicts(final_resp, value)

        # print(final_resp)
        update_resources_schema(final_resp)
        

    except Exception as e:
        raise Exception(str(e))
    
def duplicate_by_slug(slug, user):
    try:
        form = mongodb.get_record('forms', {'slug': slug})
        if not form:
            return {'msg': 'Formulario no existe'}, 404
        
        form['name'] = form['name'] + ' (copia)'
        form['slug'] = form['slug']
        form['fields'] = [field for field in form['fields']]

        form.pop('_id')
        
        return create(form, user)

    except Exception as e:
        return {'msg': str(e)}, 500
    
# Funcion que hace un merge entre dos diccionarios
def merge_dicts(dict1, dict2):
    for key, value in dict2.items():
        if isinstance(value, dict):
            if key not in dict1:
                dict1[key] = {}
            merge_dicts(dict1.get(key, {}), value)
        else:
            dict1[key] = value