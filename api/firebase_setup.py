# firebase_setup.py
import firebase_admin
from firebase_admin import credentials
import os
import logging

logger = logging.getLogger(__name__)

def initialize_firebase():
    try:
        creds_path = './config/credentials.json'
        
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
