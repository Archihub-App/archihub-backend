from flask import Blueprint

bp = Blueprint('userTasks', __name__)

from app.api.userTasks import routes