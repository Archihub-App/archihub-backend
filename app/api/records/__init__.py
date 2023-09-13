from flask import Blueprint

bp = Blueprint('records', __name__)

from app.api.records import routes