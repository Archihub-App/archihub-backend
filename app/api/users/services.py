from app.api.users import bp
from flask import jsonify
from app.utils import DatabaseHandler

mongodb = DatabaseHandler('sim-backend-prod')

# Nuevo servicio para buscar un usuario por su username
def get_user(username):
    user = mongodb.get_record('users', {'username': username})
    if not user:
        return None
    return user