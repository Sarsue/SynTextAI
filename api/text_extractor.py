import io
import logging
from pdf_extracter import extract_document_hierarchy
from llm_service import  extract_image_text
import base64
import numpy as np
import os 
from utils import chunk_text

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

"""
        I Infer from reading PDF's and describing images mentally.
        The best way to get data extracted out of PDF's and Images for the machine would be 
        A multi modal model describing an IMAGE. 
        A multi modal model reading a PDF Page
"""

def extract_text_from_image(image_data):
    logging.info("Extracting text from IMAGE...")
    try:
        data = []
        encoded_data = base64.b64encode(image_data).decode('utf-8')
        image_text =  extract_image_text(encoded_data)
        
        data.append({
                    "page_number": 0,
                    "content": image_text,
                    "chunks" : chunk_text(image_text)
        })
        return data
    except Exception as e:
        logging.error(f"Error extracting text from image: {e}")
        return None

def extract_data(file_data, file_extension):
    logging.info("processing file...")
    try:
        if file_extension == 'pdf':
            result = extract_document_hierarchy(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
            result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            data = []
            txt_data = file_data.decode('utf-8')
            # Add metadata to each chunk
            data.append({
                    "page_number": 0,
                    "content": txt_data,
                    "chunks" : chunk_text(txt_data)
            })
   
            result = data
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]
    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result

if __name__ == "__main__":
    # Example usage with a video file
    current_directory = os.getcwd()
    print(current_directory) 

