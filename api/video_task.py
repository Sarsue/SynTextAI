import io
import logging
import numpy as np
import ffmpeg
import whisper
from celery import shared_task

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

# Initialize the Whisper model (loaded once for efficiency)
model = whisper.load_model("medium")

def extract_audio_to_memory(video_data):
    """Extract audio directly from video to memory using FFmpeg."""
    logging.info("Extracting audio from video...")
    try:
        out, _ = (
            ffmpeg
            .input('pipe:0', format='mp4')
            .output('pipe:', format='wav', acodec='pcm_s16le', ac=1, ar='16000')
            .run(input=video_data, capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
        return io.BytesIO(out)
    except ffmpeg.Error as e:
        logging.error(f"Error extracting audio: {e.stderr.decode()}")
        return None

def transcribe_audio(audio_data):
    """Transcribe audio using Whisper."""
    logging.info("Transcribing audio...")
    try:
        audio_bytes = audio_data.getvalue()
        audio_array = np.frombuffer(audio_bytes, np.int16) / 32768.0  # Scale to [-1, 1]

        if audio_array.ndim != 1 or audio_array.size == 0:
            raise ValueError("Invalid audio array.")

        result = model.transcribe(audio_array, fp16=False)  # Disable FP16 if using CPU
        return result["text"]
    except Exception as e:
        logging.error(f"Error during transcription: {e}")
        return None

@shared_task
def process_video_task(video_data):
    """Celery task to extract and transcribe text from video."""
    logging.info("Processing video via Celery task...")
    audio_data = extract_audio_to_memory(video_data)

    if not audio_data:
        logging.error("Audio extraction failed.")
        return None

    text = transcribe_audio(audio_data)
    return text
