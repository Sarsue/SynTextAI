from flask import Flask, jsonify
from flask_cors import CORS
from db.docsynth_store import DocSynthStore
from api.users import users_bp
from api.histories import histories_bp
from api.messages import messages_bp
from api.files import files_bp, start_workers
from api.subscriptions import subscriptions_bp
from dotenv import load_dotenv
import os
from firebase_setup import initialize_firebase

load_dotenv()

def create_app():
    app = Flask(__name__)

    # Initialize Firebase
    initialize_firebase()

    # Set up CORS
    origin = os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000')
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

    # Add a welcome route
    @app.route('/')
    def welcome():
        return jsonify(message="Welcome to SynthTextAI API!")

    return app


