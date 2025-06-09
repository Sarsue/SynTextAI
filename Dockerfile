# Stage 1: Build the frontend
FROM node:18-alpine AS build-step
WORKDIR /app/frontend

# Copy only package files first to leverage caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies (including dev dependencies needed for build)
RUN npm ci

# Copy the remaining files and build
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# Stage 2: Set up the Python backend with FFmpeg, Whisper, and dependencies
FROM python:3.10-slim AS base

# Install system dependencies including FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    supervisor && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy backend files
COPY api/ ./api/

# Install Python dependencies
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Set environment variable for Whisper model directory
ENV WHISPER_CACHE_DIR=/app/models

# Download the 1.5GB Whisper model and store it in the image
RUN mkdir -p $WHISPER_CACHE_DIR && \
    pip install faster-whisper && \
    python -c "from faster_whisper import WhisperModel; WhisperModel('medium', download_root='$WHISPER_CACHE_DIR')"

# Copy the frontend build from the first stage
COPY --from=build-step /app/frontend/build ./frontend/build

# Expose the application port
EXPOSE 3000

# Set the working directory to /app/api
WORKDIR /app/api

# Command to start FastAPI
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]