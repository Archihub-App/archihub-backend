from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
import json
from app.api.forms.models import Form
from app.api.forms.models import FormUpdate

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los estándares de metadatos
def get_all():
    # Obtener todos los estándares de metadatos
    lists = mongodb.get_all_records('lists')
    # Quitar todos los campos menos el nombre y la descripción
    lists = [{ 'name': form['name'], 'description': form['description'], 'slug': form['slug']} for form in lists]
    # Retornar lists
    return jsonify(lists), 200

# Nuevo servicio para crear un estándar de metadatos
def create(body):
    # Crear instancia de Form con el body del request
    form = Form(**body)
    # Insertar el estándar de metadatos en la base de datos
    new_form = mongodb.insert_record('lists', form)
    # Retornar el resultado
    return {'msg': 'Formulario creado exitosamente'}, 201

# Nuevo servicio para devolver un formulario por su slug
def get_by_slug(slug):
    # Buscar el formulario en la base de datos
    form = mongodb.get_record('lists', {'slug': slug})
    # Si el formulario no existe, retornar error
    if not form:
        return {'msg': 'Formulario no existe'}
    # quitamos el id del formulario
    form.pop('_id')
    # Parsear el resultado
    form = parse_result(form)
    # Retornar el resultado
    return form

# Nuevo servicio para actualizar un formulario
def update_by_slug(slug, body):
    # Buscar el formulario en la base de datos
    form = mongodb.get_record('lists', {'slug': slug})
    # Si el formulario no existe, retornar error
    if not form:
        return {'msg': 'Formulario no existe'}, 404
    # Crear instancia de FormUpdate con el body del request
    form_update = FormUpdate(**body)
    # Actualizar el formulario en la base de datos
    mongodb.update_record('lists', {'slug': slug}, form_update)
    # Retornar el resultado
    return {'msg': 'Formulario actualizado exitosamente'}, 200