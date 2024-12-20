import io
import logging
from pdfminer.high_level import extract_text
from llm_service import  extract_image_text
import base64
import numpy as np
import os 

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

def extract_text_from_pdf(pdf_data):
    logging.info("Extracting text from PDF...")
    try:
        return extract_text(io.BytesIO(pdf_data))
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return None

def extract_text_from_image(image_data):
    logging.info("Extracting text from IMAGE...")
    try:
        encoded_data = base64.b64encode(image_data).decode('utf-8')
        return extract_image_text(encoded_data)
    except Exception as e:
        logging.error(f"Error extracting text from image: {e}")
        return None

def process_data(file_data, file_extension):
    logging.info("processing file...")
    try:
        if file_extension == 'pdf':
            result = extract_text_from_pdf(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
            result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            result = file_data.decode('utf-8')
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]
    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result

if __name__ == "__main__":
    # Example usage with a video file
    current_directory = os.getcwd()
    pass 

