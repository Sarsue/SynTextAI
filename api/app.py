import eventlet
eventlet.monkey_patch()

from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from docsynth_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool, Redis
import os
from celery import Celery
from config import redis_celery_broker_url, redis_celery_backend_url, redis_socketio_url, redis_app_url, ssl_cert_path
from datetime import datetime
import ssl
import logging
import time
from utils import get_user_id  # Existing import

# Load environment variables
load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))


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

# Initialize SocketIO
socketio = SocketIO(cors_allowed_origins="*", async_mode='eventlet', message_queue=redis_socketio_url)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Track connections
user_connections = {}  # Maps user_id -> {sid: timestamp}

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')
    initialize_firebase()
    CORS(app, supports_credentials=True)
    
    app.config['DATABASE_URL'] = DATABASE_URL
    app.socketio = socketio  # Make socketio accessible via app

    store = DocSynthStore(database_url=DATABASE_URL)
    app.store = store

    pool = ConnectionPool.from_url(redis_celery_broker_url, max_connections=10, connection_class=StrictRedis)
    redis_client = StrictRedis(connection_pool=pool)
    app.redis_client = redis_client

    from routes.users import users_bp
    from routes.histories import histories_bp
    from routes.messages import messages_bp
    from routes.files import files_bp
    from routes.subscriptions import subscriptions_bp

    app.register_blueprint(users_bp, url_prefix="/api/v1/users")
    app.register_blueprint(histories_bp, url_prefix="/api/v1/histories")
    app.register_blueprint(messages_bp, url_prefix="/api/v1/messages")
    app.register_blueprint(files_bp, url_prefix="/api/v1/files")
    app.register_blueprint(subscriptions_bp, url_prefix="/api/v1/subscriptions")

    @app.route('/')
    def serve_react_app():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def serve_static_file(path):
        return send_from_directory(app.static_folder, path)

    # SocketIO Event Handlers
    @socketio.on('connect')
    def handle_connect():
        token = request.headers.get('Authorization')
        if not token:
            emit('connected', {'status': 'connected', 'authenticated': False})
            return
        token = token.split(' ')[1]
        status, decoded_token = get_user_id(token)
        if status and 'user_id' in decoded_token:
            user_id = decoded_token['user_id']
            if user_id not in user_connections:
                user_connections[user_id] = {}
            user_connections[user_id][request.sid] = time.time()
            logger.info(f"User {user_id} connected (sid={request.sid})")
            emit('connected', {'status': 'connected', 'authenticated': True})
        else:
            emit('connected', {'status': 'connected', 'authenticated': False})

    @socketio.on('disconnect')
    def handle_disconnect():
        for user_id, connections in list(user_connections.items()):
            if request.sid in connections:
                del connections[request.sid]
                if not connections:
                    del user_connections[user_id]
                logger.info(f"User {user_id} disconnected (sid={request.sid})")
                break
        logger.info(f"Client {request.sid} disconnected")

    @socketio.on('ping')
    def handle_ping():
        emit('pong')

    return app

def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=redis_celery_broker_url,
        backend=redis_celery_backend_url,
    )
    logger.info(f"Celery broker URL: {celery.conf.broker_url}")
    logger.info(f"Celery backend URL: {celery.conf.result_backend}")
    celery.conf.update({
        'broker_url': redis_celery_broker_url,
        'result_backend': redis_celery_backend_url,
        'broker_use_ssl': {'ssl_cert_reqs': ssl.CERT_REQUIRED, 'ssl_ca_certs': ssl_cert_path},
        'redis_backend_use_ssl': {'ssl_cert_reqs': ssl.CERT_REQUIRED, 'ssl_ca_certs': ssl_cert_path},
    })
       # Add logging to see the broker URL
 

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    celery.Task = ContextTask
    return celery

app = create_app()
celery = make_celery(app)
socketio.init_app(app)

@app.route('/health')
def health_check():
    try:
        redis_client = Redis.from_url(redis_app_url, ssl_cert_reqs=ssl.CERT_REQUIRED, ssl_ca_certs=ssl_cert_path)
        redis_client.ping()
        celery.control.ping(timeout=1.0)
        return jsonify({'status': 'healthy', 'redis': 'connected', 'celery': 'connected', 'timestamp': datetime.utcnow().isoformat()}), 200
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e), 'timestamp': datetime.utcnow().isoformat()}), 500

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=3000)