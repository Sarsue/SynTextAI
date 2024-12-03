from flask import Flask, send_from_directory
from flask_cors import CORS
from flask_sse import sse
from sqlite_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import StrictRedis, ConnectionPool  # Added connection pooling
import os
import threading
import json

# Load environment variables
load_dotenv()

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')

    # Initialize Firebase
    initialize_firebase()

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
    pubsub = app.redis_client.pubsub()
    pubsub.subscribe('task_events')  # Subscribe to Redis channel

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
    app.register_blueprint(sse, url_prefix='api/v1/stream')

    def listen_for_events():
        """Listen for events on Redis channel and push to SSE"""
        for message in pubsub.listen():
            if message['type'] == 'message':
                # Parse the message
                try:
                    event_data = json.loads(message['data'])
                    user_id = event_data['user_id']
                    status = event_data.get('status', 'unknown')   
                    # Push the message to the client via SSE
                    sse.publish(event_data, type=str(user_id))  # Publish to the SSE client with user_id
                except json.JSONDecodeError:
                    continue 

 

    @app.route('/')
    def serve_react_app():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def serve_static_file(path):
        return send_from_directory(app.static_folder, path)
    
    @app.before_request
    def start_event_listener():
        """Start Redis event listener in a separate thread"""
        thread = threading.Thread(target=listen_for_events)
        thread.daemon = True
        thread.start()


    return app

