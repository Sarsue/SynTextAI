from flask import Flask, send_from_directory
from flask_cors import CORS
from postgresql_store import DocSynthStore
from api.users import users_bp
from api.histories import histories_bp
from api.messages import messages_bp
from api.files import files_bp
from api.subscriptions import subscriptions_bp
from dotenv import load_dotenv
import os
from firebase_setup import initialize_firebase
from redis import Redis
from rq import Queue
import subprocess

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
    # Set up Redis and RQ
    redis_url = f'redis://{redis_username}:{redis_pwd}@{redis_host}:{redis_port}'
    redis_conn = Redis.from_url(redis_url)
    queue = Queue(connection=redis_conn)
    app.queue = queue

    # Start RQ worker
    worker_process = subprocess.Popen(['rq', 'worker', '--url', redis_url])

    @app.teardown_appcontext
    def cleanup(exception):
        if worker_process.poll() is None:
            worker_process.terminate()
            worker_process.wait()

    # Register Blueprints
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
