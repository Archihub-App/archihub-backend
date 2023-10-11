from flask import Blueprint

bp = Blueprint('public', __name__)

from app.api.publicApi import routes