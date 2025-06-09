# firebase_setup.py
import firebase_admin
from firebase_admin import credentials
import os
import logging

logger = logging.getLogger(__name__)

def initialize_firebase():
    try:
        # First, check for credentials file
        creds_path = './config/credentials.json'
        
        # Try to load from a file first
        cred = None
        if os.path.exists(creds_path):
            try:
                cred = credentials.Certificate(creds_path)
                logger.info(f"Initialized Firebase from credentials file: {creds_path}")
            except Exception as e:
                logger.warning(f"Failed to load credentials from {creds_path}: {e}")
        
        # If file-based credentials didn't work, try environment variables
        if cred is None:
            project_id = os.environ.get('FIREBASE_PROJECT_ID')
            private_key = os.environ.get('FIREBASE_PRIVATE_KEY')
            client_email = os.environ.get('FIREBASE_CLIENT_EMAIL')
            
            if project_id and private_key and client_email:
                # Fix newlines in private key
                private_key = private_key.replace('\\n', '\n')
                
                cred_dict = {
                    "type": "service_account",
                    "project_id": project_id,
                    "private_key": private_key,
                    "client_email": client_email
                }
                
                try:
                    cred = credentials.Certificate(cred_dict)
                    logger.info("Initialized Firebase from environment variables")
                except Exception as e:
                    logger.critical(f"Failed to initialize Firebase from env variables: {e}")
                    return
            else:
                logger.critical("No Firebase credentials found in files or environment variables")
                return
        
        # Initialize the Firebase app if it hasn't been initialized yet
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {
                'storageBucket': 'docsynth-fbb02.appspot.com',
            })
            logger.info("Firebase Admin SDK initialized successfully")
        else:
            logger.info("Firebase Admin SDK already initialized")
            
    except Exception as e:
        logger.critical(f"CRITICAL ERROR initializing Firebase Admin SDK: {e}", exc_info=True)
