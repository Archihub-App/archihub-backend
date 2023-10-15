from flask import Blueprint

bp = Blueprint('tasks', __name__)

from app.api.tasks import routes