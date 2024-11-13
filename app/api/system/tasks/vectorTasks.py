from celery import shared_task
from app.utils import DatabaseHandler
from app.utils import VectorDatabaseHandler
import os

index_handler = IndexHandler.IndexHandler()
mongodb = DatabaseHandler.DatabaseHandler()

