from flask import Blueprint

bp = Blueprint('aiservices', __name__)

from app.api.aiservices import routes