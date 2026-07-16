from flask import Blueprint

bp = Blueprint('health', __name__)

from app.api.health import routes
