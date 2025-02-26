from flask import Flask, send_from_directory
from flask_cors import CORS
from docsynth_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool  # Added connection pooling
import os
from celery import Celery
from kombu.utils.url import safequote
from websocket_server import socketio
from gevent import monkey
monkey.patch_all()

# Load environment variables
load_dotenv()
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Construct paths relative to the base directory
database_config = {
    'dbname': os.getenv("DATABASE_NAME"),
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': os.getenv("DATABASE_PORT"),
}
DATABASE_URL = (
    f"postgresql://{database_config['user']}:{database_config['password']}"
    f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
)

redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv('REDIS_HOST')
redis_port = os.getenv('REDIS_PORT')

# Path to the certificate
ssl_cert_path = os.path.join(BASE_DIR, "config", "ca-certificate.crt")

# Redis connection pool for Celery with SSL configuration
redis_url = (
    f"rediss://:{safequote(redis_pwd)}@{redis_host}:{redis_port}/0"
    "?ssl_cert_reqs=CERT_REQUIRED&ssl_ca_certs=config/ca-certificate.crt"
)

redis_connection_pool_options = {
    'ssl_cert_reqs': 'CERT_REQUIRED',
    'ssl_ca_certs': ssl_cert_path,
}

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')

    # Initialize Firebase
    initialize_firebase()

    # Set up CORS
    CORS(app, supports_credentials=True)

    # Instantiate your store with the database config
    store = DocSynthStore(database_url=DATABASE_URL)
    app.store = store

    # Redis Configuration with Connection Pooling
    pool = ConnectionPool.from_url(
        redis_url, 
        max_connections=10, 
        connection_class=StrictRedis,
        **redis_connection_pool_options
    )
    redis_client = StrictRedis(connection_pool=pool)
    app.redis_client = redis_client  # Make Redis available in your app

    # Update SocketIO configuration
    socketio.init_app(app,
        cors_allowed_origins="*",
        ping_timeout=60,
        ping_interval=25,
        async_mode='gevent',  # Make sure gevent is properly installed
        message_queue=redis_url,
        engineio_logger=True,
        logger=True,
        async_handlers=True
    )

    # Register Blueprints
    from routes.users import users_bp
    from routes.histories import histories_bp
    from routes.messages import messages_bp
    from routes.files import files_bp
    from routes.subscriptions import subscriptions_bp
    from routes.logs import logs_bp

    app.register_blueprint(users_bp, url_prefix="/api/v1/users")
    app.register_blueprint(histories_bp, url_prefix="/api/v1/histories")
    app.register_blueprint(messages_bp, url_prefix="/api/v1/messages")
    app.register_blueprint(files_bp, url_prefix="/api/v1/files")
    app.register_blueprint(subscriptions_bp, url_prefix="/api/v1/subscriptions")
    app.register_blueprint(logs_bp,  url_prefix="/api/v1/logs")
  
    @app.route('/')
    def serve_react_app():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def serve_static_file(path):
        return send_from_directory(app.static_folder, path)

    return app

def make_celery(app):
    # Initialize Celery with the application's import name, broker, and backend
    celery = Celery(
        app.import_name,
        backend=redis_url,
        broker=redis_url,
    )
    celery.conf.update({
        'broker_url': redis_url,
        'result_backend': redis_url,
        'broker_transport_options': redis_connection_pool_options,
        'task_time_limit': 900,
        'task_soft_time_limit': 600,
        'worker_prefetch_multiplier': 1,
        'broker_connection_retry_on_startup': True,
        'broker_connection_max_retries': None,
    })

    # Ensure that Celery tasks run within the Flask application context
    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery

# Initialize the Flask app and Celery
app = create_app()
celery = make_celery(app)

if __name__ == '__main__':
    app = create_app()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
