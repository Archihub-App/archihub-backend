from flask import Blueprint

bp = Blueprint('resources', __name__)

from app.api.resources import routes