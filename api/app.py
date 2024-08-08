from flask import Flask, send_from_directory
from flask_cors import CORS
from postgresql_store import DocSynthStore
from dotenv import load_dotenv
from celery_worker import celery_app  # Import here to avoid circular import
import os
from firebase_setup import initialize_firebase

load_dotenv()

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')

    # Initialize Firebase
    initialize_firebase()

    # Set up CORS
    CORS(app, supports_credentials=True)

    # Database configuration
    db_name = os.getenv("DATABASE_NAME")
    db_user = os.getenv("DATABASE_USER")
    db_pwd = os.getenv("DATABASE_PASSWORD")
    db_host = os.getenv("DATABASE_HOST")
    db_port = os.getenv("DATABASE_PORT")

    database_config = {
        'dbname': db_name,
        'user': db_user,
        'password': db_pwd,
        'host': db_host,
        'port': db_port
    }

    store = DocSynthStore(database_config) 
    app.store = store

    redis_username = os.getenv('REDIS_USERNAME')
    redis_pwd = os.getenv('REDIS_PASSWORD')
    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")

    # Celery configuration
    app.config.update(
        CELERY_BROKER_URL=f'redis://:{redis_pwd}@{redis_host}:{redis_port}/0',
        CELERY_RESULT_BACKEND=f'redis://:{redis_pwd}@{redis_host}:{redis_port}/0'
    )

    celery_app.conf.update(app.config)

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

    @app.route('/')
    def serve_react_app():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def serve_static_file(path):
        return send_from_directory(app.static_folder, path)

    return app

def create_celery_app(app=None):
    if app is None:
        app = create_app()
    celery_app.conf.update(app.config)
    return celery_app
