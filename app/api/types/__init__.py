from flask import Blueprint

bp = Blueprint('types', __name__)

from app.api.types import routes
from app.api.types import public_routes