from flask import Blueprint

bp = Blueprint('snaps', __name__)

from app.api.snaps import routes