from flask import Blueprint

bp = Blueprint('forms', __name__)

from app.api.forms import routes