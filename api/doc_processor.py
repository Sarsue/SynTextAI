import io
import logging
from pdfminer.high_level import extract_text
from PIL import Image
import pytesseract
from llm_service import prompt_llm
import re 

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

def qna(data):
    try:
        data_prompt = (
            f"Based on the following text: {data}\n"
            f"Generate questions and answers.\n"
            f"Your response should be related to {data}.\n"
            f"Question 1: What is a relevant question?\n"
            f"Answer 1: A corresponding answer.\n"
            f"Question 2: What is another relevant question?\n"
            f"Answer 2: Another corresponding answer.\n"
        )
        response = prompt_llm(data_prompt)
        
        # Use regex to extract questions and answers based on the structure
        question_pattern = r"Question \d+: (.*)"
        answer_pattern = r"Answer \d+: (.*)"
        
        questions = re.findall(question_pattern, response)
        answers = re.findall(answer_pattern, response)

        return {"questions": questions, "answers": answers}

    except Exception as e:
        logging.error(f"Error in summarizer: {e}")
        return {"questions": [], "answers": []}

def get_chunks(data):
    chunk_prompt = f"""I have a large text document that needs to be semantically chunked into meaningful sections. Each chunk should contain a coherent segment of information, such as a topic, subtopic, or a logical unit of text. Below is an example of the text, followed by the desired chunking:

    Text: "Machine learning is a field of artificial intelligence that uses statistical techniques to give computer systems the ability to learn from data. It is seen as a part of artificial intelligence. Machine learning algorithms build a model based on sample data, known as training data, in order to make predictions or decisions without being explicitly programmed to perform the task. The term machine learning was coined in 1959 by Arthur Samuel, an American IBMer and pioneer in the field of computer gaming and artificial intelligence. Artificial intelligence is the broader concept of machines being able to carry out tasks in a way that we would consider smart. It includes machine learning as well as other techniques."

    Chunking:
    1. "Machine learning is a field of artificial intelligence that uses statistical techniques to give computer systems the ability to learn from data. It is seen as a part of artificial intelligence."
    2. "Machine learning algorithms build a model based on sample data, known as training data, in order to make predictions or decisions without being explicitly programmed to perform the task."
    3. "The term machine learning was coined in 1959 by Arthur Samuel, an American IBMer and pioneer in the field of computer gaming and artificial intelligence."
    4. "Artificial intelligence is the broader concept of machines being able to carry out tasks in a way that we would consider smart. It includes machine learning as well as other techniques."

    Please process the following text and provide the semantically chunked output:

    {data}"""
    
    try:
        response = prompt_llm(chunk_prompt)
        chunks = response.split("\n")
        cleaned_chunks = [chunk.split(". ", 1)[1].strip('"') for chunk in chunks if ". " in chunk]
        return {"chunks": cleaned_chunks, "data": data}
    
    except Exception as e:
        logging.error(f"Error in get_chunks: {e}")
        return {"chunks": [], "data": data}

def extract_text_from_pdf(pdf_data):
    data = []
    try:
        logging.info("processing PDF")
        text = extract_text(io.BytesIO(pdf_data))
        pages = text.split('\f')  # Split by form feed character for pages
        for page_number, page_text_data in enumerate(pages):
            result = get_chunks(page_text_data)
            result["page_number"] = page_number + 1
            data.append(result)
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        data.append({"extractpdferror": str(e)})
    
    return data

def extract_text_from_image(image_data):
    data = []
    try:
        image = Image.open(io.BytesIO(image_data))
        image_data = pytesseract.image_to_string(image)
        result = get_chunks(image_data)
        result["page_number"] = 1
        data.append(result)
    except Exception as e:
        logging.error(f"Error extracting text from image: {e}")
        data.append({"extractimageerror": str(e)})
    
    return data

def extract_text_from_txt(txt_data):
    data = []
    try:
        result = get_chunks(txt_data)
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
            result = extract_text_from_pdf(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
            result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            result = extract_text_from_txt(file_data)
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]

    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result
