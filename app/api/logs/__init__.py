from flask import Blueprint

bp = Blueprint('logs', __name__)

from app.api.logs import routes