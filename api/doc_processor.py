import io
import logging
from pdfminer.high_level import extract_text
from llm_service import prompt_llm, extract_image_text
import base64
import ffmpeg
import whisper
import numpy as np
import os 

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

def extract_audio_to_memory(video_input):
    """Extract audio directly to memory using FFmpeg."""
    try:
        input_args = {'pipe': True} if isinstance(video_input, (io.BytesIO, io.BufferedReader)) else {'filename': video_input}
        input_data = video_input.read() if isinstance(video_input, (io.BytesIO, io.BufferedReader)) else None

        out, _ = (
            ffmpeg
            .input('pipe:0' if input_data else video_input, format='mp4')
            .output('pipe:', format='wav', acodec='pcm_s16le', ac=1, ar='16000')  # Ensure output is WAV
            .run(input=input_data, capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
        return io.BytesIO(out)
    except ffmpeg.Error as e:
        logging.error(f"Error extracting audio: {e.stderr.decode()}")
        return None

def transcribe_audio(audio_data):
    """Transcribe audio using Whisper."""
    model = whisper.load_model("medium")

    try:
        # Convert audio data to numpy array for Whisper
        audio_bytes = audio_data.getvalue()  # Extract bytes from BytesIO
        audio_array = np.frombuffer(audio_bytes, np.int16)  # Use int16 if your audio is PCM 16-bit

        # Ensure the array is 1D and has a valid shape
        if audio_array.ndim != 1:
            raise ValueError("Audio array must be 1D.")
        if audio_array.size == 0:
            raise ValueError("Audio array is empty.")

        logging.info(f"Audio array shape: {audio_array.shape}, dtype: {audio_array.dtype}")

        # Normalize audio if needed (Whisper expects floats)
        audio_array = audio_array.astype(np.float32) / 32768.0  # Scale to [-1, 1]

        # Transcribe the audio
        result = model.transcribe(audio_array, fp16=False)  # Disable FP16 on CPU
        return result["text"]
    except Exception as e:
        logging.error(f"Error during transcription: {e}")
        return None

def extract_text_from_video(video_data):
    """Extract audio and transcribe text from video."""
    video_bytes = io.BytesIO(video_data)
    audio_data = extract_audio_to_memory(video_bytes)
    if not audio_data:
        logging.error("Audio extraction failed.")
        return None

    text = transcribe_audio(audio_data)
    return text

def process_file(file_data, file_extension):
    video_extensions = ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "mpeg", "mpg", "3gp"]
    try:
        if file_extension == 'pdf':
            result = extract_text_from_pdf(file_data)
        elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
            result = extract_text_from_image(file_data)
        elif file_extension == 'txt':
            result = file_data.decode('utf-8')
        elif file_extension in video_extensions:
            result = extract_text_from_video(file_data)
        else:
            result = [{"error": f"Unsupported file type: {file_extension}"}]
    except Exception as e:
        logging.error(f"Error processing file: {e}")
        result = [{"processfileerror": str(e)}]

    return result

if __name__ == "__main__":
    # Example usage with a video file
    current_directory = os.getcwd()
    file_name = "api/pilotshowepisode2.mp4"  # Replace with your actual file name
    file_path = os.path.join(current_directory, file_name)

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"The file '{file_path}' does not exist.")

    # Test with in-memory video
    with open(file_path, "rb") as video_file:
        video_data= video_file.read()
        print(process_file(video_data, 'mp4'))


