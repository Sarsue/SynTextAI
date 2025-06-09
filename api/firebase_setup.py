# firebase_setup.py
import firebase_admin
from firebase_admin import credentials
import os
import logging

logger = logging.getLogger(__name__)

def initialize_firebase():
    try:
        # Try multiple potential paths for the credentials file
        potential_paths = [
            './config/credentials.json',
            './api/config/credentials.json',
            '/app/api/config/credentials.json'
        ]
        
        creds_path = None
        for path in potential_paths:
            if os.path.exists(path):
                creds_path = path
                logger.info(f"Found Firebase credentials at: {os.path.abspath(path)}")
                break
                
        if creds_path is None:
            creds_path = './config/credentials.json'  # Default for error message
        
        if not os.path.exists(creds_path):
            logger.critical(f"Firebase Admin SDK credentials file NOT FOUND at: {os.path.abspath(creds_path)}")
            return

        cred = credentials.Certificate(creds_path)
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {
                'storageBucket': 'docsynth-fbb02.appspot.com',
            })
            logger.info("Firebase Admin SDK initialized successfully.")
        else:
            logger.info("Firebase Admin SDK already initialized.")
            
    except Exception as e:
        logger.critical(f"CRITICAL ERROR initializing Firebase Admin SDK: {e}", exc_info=True)
