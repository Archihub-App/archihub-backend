from flask import Blueprint

bp = Blueprint('search', __name__)

from app.api.search import routes