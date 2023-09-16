from flask import Blueprint

bp = Blueprint('system', __name__)

from app.api.system import routes