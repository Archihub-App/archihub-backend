from flask import Blueprint

bp = Blueprint('usertasks', __name__)

from app.api.usertasks import routes