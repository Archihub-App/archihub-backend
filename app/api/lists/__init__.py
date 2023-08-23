from flask import Blueprint

bp = Blueprint('lists', __name__)

from app.api.lists import routes