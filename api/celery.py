from celery import Celery
import os

def make_celery(app_name):
    celery = Celery(
        app_name,
        backend=os.getenv('CELERY_RESULT_BACKEND'),
        broker=os.getenv('CELERY_BROKER_URL')
    )
    celery.conf.update({
        'broker_url': os.getenv('CELERY_BROKER_URL'),
        'result_backend': os.getenv('CELERY_RESULT_BACKEND')
    })
    return celery

celery_app = make_celery('api')
