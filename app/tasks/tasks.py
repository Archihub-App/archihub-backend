from celery import shared_task
import time

@shared_task(ignore_result=False, name='Procesamiento de archivos')
def add(x):
    # add a delay of 10 seconds
    time.sleep(10)

    print(x)
    return 'ok'