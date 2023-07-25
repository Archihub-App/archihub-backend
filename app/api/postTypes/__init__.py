from flask import Blueprint

bp = Blueprint('postTypes', __name__)

from app.api.postTypes import routes