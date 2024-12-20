from flask import Flask, send_from_directory
from flask_cors import CORS
from sqlite_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool  # Added connection pooling
import os
from celery import Celery

# Load environment variables

# from transformers import pipeline

# # Load a small model for text classification or summarization
# text_summarizer = pipeline("summarization", model="t5-small", device=-1)  # Runs on CPU

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
    #Initialize Flask-SSE
    app.register_blueprint(sse_bp, url_prefix='/api/v1/stream')
   

    @app.route('/')
    def serve_react_app():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def serve_static_file(path):
        return send_from_directory(app.static_folder, path)
    

    return app

def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=f"filesystem://{CELERY_FILESYSTEM_PATH}",
        backend=f"file://{CELERY_FILESYSTEM_PATH}"
    )
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


app = create_app()
celery = make_celery(app)
