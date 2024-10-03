from flask import Blueprint

bp = Blueprint('resources', __name__)

from app.api.resources import routes
from app.api.resources import public_routes