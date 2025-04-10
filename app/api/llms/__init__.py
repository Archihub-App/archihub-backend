from flask import Blueprint

bp = Blueprint('llms', __name__)

from app.api.llms import routes