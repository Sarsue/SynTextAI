import io
import logging
import numpy as np
import ffmpeg
import whisper
from celery import shared_task

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

# Initialize the Whisper model once for efficiency
model = whisper.load_model("base")  # Use "tiny" or "small" for lighter models if needed

def extract_audio_to_memory_chunked(video_data, chunk_size=10):
    """Extract audio in chunks from video to reduce memory usage."""
    logging.info("Extracting audio in chunks...")
    try:
        process = (
            ffmpeg
            .input('pipe:0', format='mp4')
            .output('pipe:', format='wav', acodec='pcm_s16le', ac=1, ar='16000')
            .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True)
        )

        # Send video data to ffmpeg process and read output in chunks
        output_chunks = []
        while True:
            chunk = process.stdout.read(chunk_size * 1024 * 1024)  # Read 10MB chunks
            if not chunk:
                break
            output_chunks.append(io.BytesIO(chunk))

        # Close ffmpeg process
        process.stdin.close()
        process.wait()  # Wait for ffmpeg to finish processing

        return output_chunks
    except ffmpeg.Error as e:
        logging.error(f"Error extracting audio: {e.stderr.decode()}")
        return []

def transcribe_audio_chunked(audio_stream):
    """Transcribe audio from chunks using Whisper."""
    logging.info("Transcribing audio in chunks...")
    full_transcription = ""

    try:
        for audio_chunk in audio_stream:
            audio_bytes = audio_chunk.getvalue()
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            if audio_array.ndim != 1 or audio_array.size == 0:
                logging.warning("Skipping empty or invalid audio chunk.")
                continue

            result = model.transcribe(audio_array, fp16=False)  # Use CPU-friendly mode
            full_transcription += result["text"] + " "

        return full_transcription.strip()
    except Exception as e:
        logging.error(f"Error during transcription: {e}")
        return None

@shared_task
def process_video_task(video_data):
    """Celery task to extract and transcribe text from video in chunks."""
    logging.info("Processing video via Celery task...")

    # Extract audio in chunks to reduce memory usage
    audio_stream = extract_audio_to_memory_chunked(video_data)

    # Transcribe audio chunks incrementally
    transcription = transcribe_audio_chunked(audio_stream)

    if transcription:
        logging.info("Video transcription completed successfully.")
        return transcription
    else:
        logging.error("Failed to transcribe video.")
        return None
