import io
import logging
from pdfminer.high_level import extract_text
from llm_service import prompt_llm,extract_image_text
import base64

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')



def extract_text_from_pdf(pdf_data):
    try:
        
        return extract_text(io.BytesIO(pdf_data))
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return None
    






def extract_text_from_image(image_data):
    try:
        encoded_data = base64.b64encode(image_data).decode('utf-8')
        return extract_image_text(encoded_data)
        
    except Exception as e:
        logging.error(f"Error extracting text from image: {e}")
        return None
    
def process_file(file_data, file_extension):

    try:
        if file_extension == 'pdf':
            result = extract_text_from_pdf(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']: 
           result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            result = file_data
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]

    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result

if __name__ == "__main__":
    pdf_path = "//Users//osas//Downloads//Pitch Deck.pdf"
    # # Open and read image data
    # with open(pdf_path, "rb") as pdf_file:
    #     pdf_data = pdf_file.read()

    # # Call the process_file function with 'jpeg' as the file extension
    # result = process_file(pdf_data, 'pdf') 
    # print(result)
    # from context_processor import process_file_context
    # if(len(result.strip()) > 0):
    #     response = process_file_context(result)
    #     print(response)
    # else:
    #     print("no data")