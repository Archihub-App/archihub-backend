from flask import Blueprint

bp = Blueprint('views', __name__)

from app.api.views import routes
from app.api.views import public_routes