# firebase_setup.py
import firebase_admin
from firebase_admin import credentials
import os 

def initialize_firebase():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./config/credentials.json"
    bucket_path = 'docsynth-fbb02.appspot.com'
    cred = credentials.Certificate('./config/credentials.json')
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {
            'storageBucket': bucket_path,
        })
