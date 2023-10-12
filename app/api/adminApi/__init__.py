from flask import Blueprint

bp = Blueprint('adminApi', __name__)

from app.api.adminApi import routes