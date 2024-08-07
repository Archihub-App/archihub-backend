from flask import Blueprint

bp = Blueprint('geosystem', __name__)

from app.api.geosystem import routes
from app.api.geosystem import public_routes