from celery import Celery
import os
from firebase_setup import initialize_firebase

initialize_firebase()

redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")

def make_celery(app_name):
    celery = Celery(
        app_name,
        backend=f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_NONE',
        broker=f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_NONE'
    )
    celery.conf.update({
        'broker_url': f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_NONE',
        'result_backend': f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_NONE'
    })

    # Automatically discover tasks from specified modules
    celery.autodiscover_tasks(['routes.files'])

    return celery

# Create Celery app
celery_app = make_celery('api')

# Register the task explicitly
from routes.files import process_and_store_file  # Ensure this import is here to register the task
