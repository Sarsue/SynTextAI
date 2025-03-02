
from app import app, celery as celery_app

# Export celery for use in worker
__all__ = ('celery_app',)