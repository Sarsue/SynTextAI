import eventlet
eventlet.monkey_patch()  # This needs to be at the very top, before other imports

from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
from docsynth_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool  # Added connection pooling
import os
from celery import Celery
from urllib.parse import quote as safequote
from datetime import datetime
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

# Redis configuration
redis_host = os.getenv('REDIS_HOST')
redis_port = os.getenv('REDIS_PORT', '25061')
redis_pwd = os.getenv('REDIS_PASSWORD')
ssl_cert_path = os.getenv('SSL_CERT_PATH', '/etc/ssl/certs/ca-certificates.crt')

# Redis URL with proper SSL configuration
redis_url = (
    f"rediss://:{safequote(redis_pwd)}@{redis_host}:{redis_port}/0"
)

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
    )
    redis_client = StrictRedis(connection_pool=pool)
    app.redis_client = redis_client  # Make Redis available in your app

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

    @app.route('/health')
    def health_check():
        try:
            # Check Redis connection
            redis_client = StrictRedis.from_url(redis_url, ssl_cert_reqs='CERT_REQUIRED', ssl_ca_certs=ssl_cert_path)
            redis_client.ping()
            
            # Check Celery
            celery.control.ping(timeout=1.0)
            
            return jsonify({
                'status': 'healthy',
                'redis': 'connected',
                'celery': 'connected',
                'timestamp': datetime.utcnow().isoformat()
            }), 200
        except Exception as e:
            return jsonify({
                'status': 'unhealthy',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }), 500

    return app

def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=redis_url,
        backend=redis_url,
    )
    
    # Celery configuration with SSL
    celery.conf.update({
        'broker_url': redis_url,
        'result_backend': redis_url,
        'broker_use_ssl': {
            'ssl_cert_reqs': 'CERT_REQUIRED',
            'ssl_ca_certs': ssl_cert_path,
            'ssl_certfile': None,
            'ssl_keyfile': None,
        },
        'redis_backend_use_ssl': {
            'ssl_cert_reqs': 'CERT_REQUIRED',
            'ssl_ca_certs': ssl_cert_path,
            'ssl_certfile': None,
            'ssl_keyfile': None,
        },
        'broker_connection_retry_on_startup': True,
        'broker_connection_max_retries': 10,
        'broker_connection_timeout': 30,
        'task_time_limit': 900,
        'task_soft_time_limit': 600,
        'worker_prefetch_multiplier': 1,
    })

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
    from websocket_server import socketio  # Import only in __main__
    socketio.init_app(app, message_queue=redis_url, async_mode='eventlet')
    socketio.run(app, debug=True, host='0.0.0.0', port=3000)


