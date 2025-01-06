import io
import logging
from pdfminer.high_level import extract_text
from llm_service import  extract_image_text
import base64
import numpy as np
import os 

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

"""
        I Infer from reading PDF's and describing images mentally.
        The best way to get data extracted out of PDF's and Images for the machine would be 
        A multi modal model describing an IMAGE. 
        A multi modal model reading a PDF Page
"""
import io
import json
import logging
from pdfminer.high_level import extract_text

logging.basicConfig(level=logging.INFO)

def extract_text_from_pdf(pdf_data):
    """
    Extract text from PDF using pdfminer.
    """
    logging.info("Extracting text from PDF...")
    try:
        return extract_text(io.BytesIO(pdf_data))
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return None

def chunk_text(text, chunk_size=500):
    """
    Chunk the text into smaller pieces of a specified size.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # Ensure we do not split words in the middle
        if end < len(text) and text[end] != ' ':
            while end > start and text[end] not in [' ', '\n']:
                end -= 1

        chunks.append(chunk)
        start = end
    return chunks

def extract_pdf_chunks_with_metadata(pdf_data, chunk_size=500):
    """
    Extract text from a PDF file, chunk it, and add page metadata.
    """
    data = []  # To hold all chunks and metadata
    try:
        # Extract full text using pdfminer
        full_text = extract_text_from_pdf(pdf_data)
        if not full_text:
            logging.error("Failed to extract text from PDF.")
            return []

        # Split the extracted text by pages
        pages = full_text.split("\x0c")  # PDFMiner uses '\x0c' for page breaks
        for page_number, page_text in enumerate(pages, start=1):
            if not page_text.strip():  # Skip empty pages
                continue

            # Chunk the page text
            chunks = chunk_text(page_text, chunk_size)

            # Add metadata to each chunk
            for index, chunk in enumerate(chunks, start=1):  # Use index and chunk here
                data.append({
                    "page_number": page_number,
                    "content": chunk.strip()  # chunk is already a string now
                })
    except Exception as e:
        logging.error(f"Error processing PDF: {e}")

    return data





def extract_text_from_image(image_data):
    logging.info("Extracting text from IMAGE...")
    try:
        data = []
        encoded_data = base64.b64encode(image_data).decode('utf-8')
        image_text =  extract_image_text(encoded_data)
        chunks = chunk_text(image_text)
        # Add metadata to each chunk
        for chunk in enumerate(chunks, start=1):
            data.append({
                    "page_number": 0,
                    "content": chunk.strip()
            })
        return data
    except Exception as e:
        logging.error(f"Error extracting text from image: {e}")
        return None

def extract_data(file_data, file_extension):
    logging.info("processing file...")
    try:
        if file_extension == 'pdf':
            result = extract_pdf_chunks_with_metadata(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
            result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            data = []
            txt_data = file_data.decode('utf-8')
            chunks = chunk_text(txt_data)
            # Add metadata to each chunk
            for chunk in enumerate(chunks, start=1):
                data.append({
                        "page_number": 0,
                        "content": chunk.strip()
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

