from flask import Blueprint

bp = Blueprint('users', __name__)

from app.api.users import routes