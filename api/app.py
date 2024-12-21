from flask import Flask, send_from_directory
from flask_cors import CORS
from sqlite_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool  # Added connection pooling
import os
from celery import Celery

# Load environment variables
load_dotenv()
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Construct paths relative to the base directory
CELERY_FILESYSTEM_PATH = os.path.join(BASE_DIR, "db/")
DATABASE_PATH = os.path.join(BASE_DIR, "db/docsynth.db")

print(f"CELERY PATH: {CELERY_FILESYSTEM_PATH}")

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')

    # Initialize Firebase
    initialize_firebase()

    # Set up CORS
    CORS(app, supports_credentials=True)

    # Instantiate your store with the SQLite config
    store = DocSynthStore(database_path=DATABASE_PATH)
    app.store = store

    # Register Blueprints
    from routes.users import users_bp
    from routes.histories import histories_bp
    from routes.messages import messages_bp
    from routes.files import files_bp
    from routes.subscriptions import subscriptions_bp
    from routes.sse import sse_bp

    app.register_blueprint(users_bp, url_prefix="/api/v1/users")
    app.register_blueprint(histories_bp, url_prefix="/api/v1/histories")
    app.register_blueprint(messages_bp, url_prefix="/api/v1/messages")
    app.register_blueprint(files_bp, url_prefix="/api/v1/files")
    app.register_blueprint(subscriptions_bp, url_prefix="/api/v1/subscriptions")
    app.register_blueprint(sse_bp, url_prefix='/api/v1/stream')

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
        broker=f"filesystem://{CELERY_FILESYSTEM_PATH}",
        backend=f"file://{CELERY_FILESYSTEM_PATH}"
    )

    # Create necessary directories for the filesystem broker
    CELERY_DATA_IN = os.path.join(CELERY_FILESYSTEM_PATH, 'data_in')
    CELERY_DATA_OUT = os.path.join(CELERY_FILESYSTEM_PATH, 'data_out')
    os.makedirs(CELERY_DATA_IN, exist_ok=True)
    os.makedirs(CELERY_DATA_OUT, exist_ok=True)

    # Update configuration for the filesystem broker
    celery.conf.update(
        broker_transport_options={
            'data_folder_in': CELERY_DATA_IN,
            'data_folder_out': CELERY_DATA_OUT,
        },
        broker_connection_retry_on_startup=True  # Fixed syntax error here
    )

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
