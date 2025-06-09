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

        # Try to fix and load the JSON file if it's malformed
        try:
            import json
            with open(creds_path, 'r') as f:
                raw_json = f.read()

            # Try to parse as-is
            try:
                creds_data = json.loads(raw_json)
            except json.JSONDecodeError as e:
                logger.warning(f"Attempting to fix malformed JSON: {e}")
                # Try to fix common issues with the JSON format
                # 1. Replace single quotes with double quotes
                fixed_json = raw_json.replace("'", '"')
                # 2. Fix newlines that might be actual newline characters
                fixed_json = fixed_json.replace('\n', ' ')
                
                # Write the fixed JSON back to the file
                with open(creds_path, 'w') as f:
                    json.dump(json.loads(fixed_json), f)
                    
                logger.info("JSON file fixed and rewritten")
                
            # Now try to create the certificate
            cred = credentials.Certificate(creds_path)
        except Exception as e:
            logger.critical(f"Failed to parse Firebase credentials: {e}")
            # As a last resort, try to create from environment variable
            firebase_credentials = os.environ.get('FIREBASE_CREDENTIALS_JSON')
            if firebase_credentials:
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_creds:
                        temp_creds.write(firebase_credentials)
                        temp_path = temp_creds.name
                    cred = credentials.Certificate(temp_path)
                    logger.info("Created Firebase credentials from environment variable")
                except Exception as inner_e:
                    logger.critical(f"Failed to create Firebase credentials from environment variable: {inner_e}")
                    return
            else:
                logger.critical("No Firebase credentials available")
                return
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {
                'storageBucket': 'docsynth-fbb02.appspot.com',
            })
            logger.info("Firebase Admin SDK initialized successfully.")
        else:
            logger.info("Firebase Admin SDK already initialized.")
            
    except Exception as e:
        logger.critical(f"CRITICAL ERROR initializing Firebase Admin SDK: {e}", exc_info=True)
