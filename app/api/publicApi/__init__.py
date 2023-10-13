from flask import Blueprint

bp = Blueprint('publicApi', __name__)

from app.api.publicApi import routes