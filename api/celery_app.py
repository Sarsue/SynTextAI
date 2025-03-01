# api/celery_app.py
import eventlet
eventlet.monkey_patch()  # Must be the very first thing

from app import app, celery as celery_app

# Export celery for use in worker
__all__ = ('celery_app',)