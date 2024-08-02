from flask import Flask, jsonify,send_from_directory
from flask_cors import CORS
from db.docsynth_store import DocSynthStore
from api.users import users_bp
from api.histories import histories_bp
from api.messages import messages_bp
from api.files import files_bp
from api.subscriptions import subscriptions_bp
from dotenv import load_dotenv
import os
from firebase_setup import initialize_firebase

load_dotenv()

def create_app():
    app = Flask(__name__, static_folder='../build', static_url_path='/')




    # Initialize Firebase
    initialize_firebase()

    # Set up CORS
    CORS(app, supports_credentials=True)

    # Read the database URL from environment variables
    database_path = os.getenv('DATABASE_URL', './db/docsynth.db')
    store = DocSynthStore(database_path)
   
    app.store = store

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


