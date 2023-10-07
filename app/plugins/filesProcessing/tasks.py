from celery import shared_task
import time

@shared_task(ignore_result=False)
def add(x, y):
    print(x + y)
    return x + y