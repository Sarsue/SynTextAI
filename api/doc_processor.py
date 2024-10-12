import io
import logging
from pdfminer.high_level import extract_text
from llm_service import prompt_llm,prompt_multimodal
import re 

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

def summarize(data):
    try:
        data_prompt = (
            f"Summarize the following text: {data}\n"
            f"Generate the 4 questions and answers that best summarize the information\n"
            f""
        )
        response = prompt_llm(data_prompt)
        
        # Use regex to extract questions and answers based on the structure
        return response

    except Exception as e:
        logging.error(f"Error in summarizer: {e}")
        return {"questions": [], "answers": []}


def extract_text_from_pdf(pdf_data):
    data = []
    try:
        
        text = extract_text(io.BytesIO(pdf_data))
        pages = text.split('\f')  # Split by form feed character for pages
        for page_number, page_text_data in enumerate(pages):
            result["page_data"] = page_text_data
            result["page_number"] = page_number + 1
            data.append(result)
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        data.append({"extractpdferror": str(e)})
    
    return data



def extract_text_from_txt(txt_data):
    data = []
    try:
        result["page_data"] = txt_data
        result["page_number"] = 1
        data.append(result)
    except Exception as e:
        logging.error(f"Error extracting text from TXT file: {e}")
        data.append({"extracttexterror": str(e)})
    
    return data

def process_file(file_data, file_extension):
    result = []

    try:
        if file_extension == 'pdf':
            result  = []
           # result = extract_text_from_pdf(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
            encoded_data = base64.b64encode(file_data).decode('utf-8')
            img_data = result.append(prompt_multimodal(encoded_data))
            result["page_data"] = img_data
            result["page_number"] = 1
        elif file_extension == 'txt':
            result = extract_text_from_txt(file_data)
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]

    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result

if __name__ == "__main__":
    import base64
    image_path = "//Users//osas//Downloads//test.jpeg"
    # Open and read image data
    with open(image_path, "rb") as image_file:
        image_data = base64.b64encode(image_file.read()).decode('utf-8')

    # Call the process_file function with 'jpeg' as the file extension
    result = process_file(image_data, 'jpeg') 
    print(result)