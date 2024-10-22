from flask import Blueprint

bp = Blueprint('search', __name__)

from app.api.search import routes
from app.api.search import public_routes