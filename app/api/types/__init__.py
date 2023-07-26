from flask import Blueprint

bp = Blueprint('types', __name__)

from app.api.types import routes