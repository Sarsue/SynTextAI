import io
import logging
from pdfminer.high_level import extract_text
from llm_service import prompt_llm,extract_image_text
import base64
import ffmpeg
import whisper
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

def extract_audio_to_memory(video_file):
    """Extract audio directly to memory using FFmpeg."""
    try:
        out, _ = (
            ffmpeg
            .input(video_file)
            .output('pipe:', format='wav', acodec='pcm_s16le', ac=1, ar='16000')
            .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
        return io.BytesIO(out)
    except ffmpeg.Error as e:
        logging.error(f"Error extracting audio: {e.stderr.decode()}")
        return None

def transcribe_audio(audio_data):
    """Transcribe audio using Whisper."""
    model = whisper.load_model("medium")  # Adjust model size as needed

    try:
        audio_file = io.BytesIO(audio_data)
        result = model.transcribe(audio_file)
        return result["text"]
    except Exception as e:
        logging.error(f"Error during transcription: {e}")
        return None

def extract_text_from_video(video_file):
    """Extract audio and transcribe text from video."""
    audio_data = extract_audio_to_memory(video_file)
    if not audio_data:
        logging.error("Audio extraction failed.")
        return None
    text = transcribe_audio(audio_data)
    return text

    
def process_file(file_data, file_extension):
    video_extensions = [
    "mp4", "mkv", "avi", "mov", "wmv", 
    "flv", "webm", "mpeg", "mpg", "3gp"
]
    try:
        if file_extension == 'pdf':
            result = extract_text_from_pdf(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']: 
           result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            result = file_data
        elif file_extension in video_extensions:
            result = extract_text_from_video(file_data)
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]

    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result

if __name__ == "__main__":
    pdf_path = "//Users//osas//Downloads//Esther-Vilar-The-Manipulated-Man.pdf"
    # Open and read image data
    with open(pdf_path, "rb") as pdf_file:
        pdf_data = pdf_file.read()

    # Call the process_file function with 'jpeg' as the file extension
    result = process_file(pdf_data, 'pdf') 
   