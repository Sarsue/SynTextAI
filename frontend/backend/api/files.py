import os
import logging
from flask import Blueprint, request, jsonify, current_app
from utils import get_user_id
from doc_processor import process_file
from google.cloud import storage

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

bucket_name = 'docsynth-fbb02.appspot.com'
files_bp = Blueprint("files", __name__, url_prefix="/api/v1/files")

def get_id_helper(success, user_info):
    if not success:
        return jsonify(user_info), 401
    return user_info['user_id']

def delete_from_gcs(user_id, file_name):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        user_folder = f"{user_id}/"
        blob = bucket.blob(user_folder + file_name)
        blob.delete()
        logging.info(f"Deleted {file_name} from GCS.")
    except Exception as e:
        logging.error(f"Error deleting from GCS: {e}")

def upload_to_gcs(file_path, user_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        user_folder = f"{user_id}/"
        blob = bucket.blob(user_folder + filename)
        blob.upload_from_filename(file_path)
        blob.make_public()
        file_url = blob.public_url
        logging.info(f"Uploaded {filename} to GCS: {file_url}")
        return file_url
    except Exception as e:
        logging.error(f"Error uploading to GCS: {e}")
        return None

def process_and_store_file(file_path, user_id, file_id, filename):
    from firebase_setup import initialize_firebase
    initialize_firebase()

    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found at path: {file_path}")

            # Process the file
            doc_info = process_file(file_path)
            print(f"Document info: {doc_info}")

            # Verify file exists after processing
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found at path (post-processing check): {file_path}")

            # Upload to GCS
            file_url = upload_to_gcs(file_path, user_id, filename)
            if file_url:
                store = current_app.store
                store.add_file(file_id, filename, file_url, doc_info)
                print(f"Finished processing and storing file: {filename}")

        except Exception as e:
            logging.error(f"Error processing file {filename}: {e}")

        finally:
            # Clean up: remove file from source documents folder
            if os.path.exists(file_path):
                os.remove(file_path)
                logging.info(f"Removed local file: {file_path}")

@files_bp.route('', methods=['GET'])
def retrieve_files():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = store.get_user_id_from_email(user_info['email'])
        files = store.get_files_for_user(user_id)
        return jsonify(files)
    except Exception as e:
        logging.error(str(e))
        return jsonify({'error': str(e)}), 500

@files_bp.route('', methods=['POST'])
def save_file():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = get_id_helper(success, user_info)
        file_id = store.get_user_id_from_email(user_info['email'])

        if not request.files:
            logging.error('No files provided')
            return jsonify({'error': 'No files provided'}), 400

        # Define the source documents folder path
        source_documents_folder = './source_documents/'

        for file_key, file in request.files.items():
            print(f"Received file: {file.filename}")

            # Save the file to the source documents folder
            file_path = os.path.join(source_documents_folder, file.filename)
            file.save(file_path)

            # Process and store the file
            process_and_store_file(file_path, user_id, file_id, file.filename)
            print(f"Processed and stored file: {file.filename}")

        return jsonify({'message': 'File processing complete. It will appear in the knowledge base management section in settings.'})

    except Exception as e:
        print(str(e))
        return jsonify({'error': str(e)}), 500

@files_bp.route('/<int:fileId>', methods=['DELETE'])
def delete_file(fileId):
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = get_id_helper(success, user_info)
        file_id = store.get_user_id_from_email(user_info['email'])
        file_dict = store.delete_file_entry(file_id, fileId)
        delete_from_gcs(user_id, file_dict['file_name'])
        return '', 204
    except Exception as e:
        logging.error(str(e))
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Your Flask app initialization code here
    from app import create_app
    app = create_app()
    app.register_blueprint(files_bp)
    app.run(debug=True)
