from app.api.users import bp
from flask import jsonify

# Nuevo servicio para buscar un usuario por su username
def get_user(username):
    return jsonify({'username': username}), 200