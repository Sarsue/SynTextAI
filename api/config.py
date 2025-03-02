import os
import ssl
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Redis configuration
redis_host = os.getenv('REDIS_HOST')
redis_port = os.getenv('REDIS_PORT', '25061')
redis_pwd = os.getenv('REDIS_PASSWORD')
ssl_cert_path = os.path.join(BASE_DIR, "config", "ca-certificate.crt")

# SSL configuration
ssl_conf = {
    'ssl_cert_reqs': ssl.CERT_REQUIRED,
    'ssl_ca_certs': ssl_cert_path,
}

# Redis URLs - handle password properly
def get_redis_url(db_number):
    if redis_pwd:
        return f"rediss://:{quote_plus(redis_pwd)}@{redis_host}:{redis_port}/{db_number}?ssl_cert_reqs={ssl.CERT_REQUIRED}&ssl_ca_certs={ssl_cert_path}"
    return f"rediss://{redis_host}:{redis_port}/{db_number}?ssl_cert_reqs={ssl.CERT_REQUIRED}&ssl_ca_certs={ssl_cert_path}"

# Define Redis URLs for different purposes
redis_celery_broker_url = get_redis_url(0)
redis_celery_backend_url = get_redis_url(1)
redis_socketio_url = get_redis_url(2)
redis_app_url = get_redis_url(3)