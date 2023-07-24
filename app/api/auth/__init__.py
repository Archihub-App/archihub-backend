from flask import Blueprint

bp = Blueprint('auth', __name__)

from app.api.auth import routes