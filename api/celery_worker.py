from celery import Celery
import os

redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")

CELERY_BROKER_URL=f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}/0',
CELERY_RESULT_BACKEND=f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}/0'

def make_celery(app_name):
    celery = Celery(
        app_name,
        backend=f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}/0',
        broker=f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}/0'
    )
    celery.conf.update({
        'broker_url': f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}/0',
        'result_backend': f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}/0'
    })
    return celery

celery_app = make_celery('api')
