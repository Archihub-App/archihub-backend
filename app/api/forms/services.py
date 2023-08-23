from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.api.forms.models import Form
from app.api.forms.models import FormUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los estándares de metadatos
@lru_cache(maxsize=1)
def get_all():
    try:
        # Obtener todos los estándares de metadatos
        forms = mongodb.get_all_records('forms', {}, [('name', 1)])
        # Quitar todos los campos menos el nombre y la descripción
        forms = [{ 'name': form['name'], 'description': form['description'], 'slug': form['slug']} for form in forms]
        # Retornar forms
        return jsonify(forms), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un estándar de metadatos
def create(body, user):
    # Crear instancia de Form con el body del request
    try:
        form = Form(**body)
        # Insertar el estándar de metadatos en la base de datos
        new_form = mongodb.insert_record('forms', form)
        # Registrar el log
        register_log(user, log_actions['form_create'])
        # Limpiar la cache
        get_by_slug.cache_clear()
        get_all.cache_clear()
        # Retornar el resultado
        return {'msg': 'Formulario creado exitosamente'}, 201
    except Exception as e:
            return {'msg': str(e)}, 500

# Nuevo servicio para devolver un formulario por su slug
@lru_cache(maxsize=30)
def get_by_slug(slug):
    try:
        # Buscar el formulario en la base de datos
        form = mongodb.get_record('forms', {'slug': slug})
        # Si el formulario no existe, retornar error
        if not form:
            return {'msg': 'Formulario no existe'}
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
        form = mongodb.get_record('forms', {'slug': slug})
        # Si el formulario no existe, retornar error
        if not form:
            return {'msg': 'Formulario no existe'}, 404
        # Crear instancia de FormUpdate con el body del request
        form_update = FormUpdate(**body)
        # Actualizar el formulario en la base de datos
        mongodb.update_record('forms', {'slug': slug}, form_update)
        # Registrar el log
        register_log(user, log_actions['form_update'])
        # Limpiar la cache
        get_all.cache_clear()
        get_by_slug.cache_clear()
        # Retornar el resultado
        return {'msg': 'Formulario actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500