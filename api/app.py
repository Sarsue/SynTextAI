from flask import Flask, send_from_directory
from flask_cors import CORS
from flask_sse import sse
from sqlite_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool  # Added connection pooling
import os
from celery import Celery

# Load environment variables
load_dotenv()

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')

    # Initialize Firebase
    initialize_firebase()
    app.config['REDIS_URL'] = os.getenv('REDIS_URL')
    app.config['CELERY_BROKER_URL'] = app.config['REDIS_URL']
    app.config['CELERY_RESULT_BACKEND'] = app.config['REDIS_URL']
    app.config['SSE_REDIS_URL'] = app.config['REDIS_URL']


    # Set up CORS
    CORS(app, supports_credentials=True)

    # Database configuration
    # database_config = {
    #     'dbname': os.getenv("DATABASE_NAME"),
    #     'user': os.getenv("DATABASE_USER"),
    #     'password': os.getenv("DATABASE_PASSWORD"),
    #     'host': os.getenv("DATABASE_HOST"),
    #     'port': os.getenv("DATABASE_PORT"),
    # }
    # store = DocSynthStore(database_config)
    # Database configuration for SQLite

    # Instantiate your store with the SQLite config
    store = DocSynthStore(os.getenv("DATABASE_PATH"))

    app.store = store

    # Redis Configuration with Connection Pooling
    redis_username = os.getenv('REDIS_USERNAME')
    redis_pwd = os.getenv('REDIS_PASSWORD')
    redis_host = os.getenv('REDIS_HOST')
    redis_port = os.getenv('REDIS_PORT')

    # Redis connection pool for Celery
    redis_url = f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0'
    pool = ConnectionPool.from_url(redis_url, max_connections=10)  # Set a max connection pool

    redis_client = StrictRedis(connection_pool=pool)
    app.redis_client = redis_client  # Make Redis available in your app

    # Register Blueprints
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
    #Initialize Flask-SSE
    app.register_blueprint(sse, url_prefix='/api/v1/stream')
   

    @app.route('/')
    def serve_react_app():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def serve_static_file(path):
        return send_from_directory(app.static_folder, path)
    

    return app

def make_celery(app):
    celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'], broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery

app = create_app()
celery = make_celery(app)
