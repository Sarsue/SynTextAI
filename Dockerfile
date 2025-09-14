# Stage 1: Build the frontend
FROM node:18-alpine AS build-step

# No build-time secrets - these will be injected at runtime
ENV PUBLIC_URL=/
ENV NODE_ENV=production

# Only include non-sensitive environment variables
ENV REACT_APP_ENV=production

WORKDIR /app/frontend

# Copy only package files first to leverage caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies (including dev dependencies needed for build)
RUN npm ci

# Copy the remaining files
COPY frontend/ ./

# Install production dependencies and build
RUN npm install && \
    npm run build && \
    npm cache clean --force

# Stage 2: Set up the Python backend with FFmpeg, Whisper, and dependencies
FROM python:3.10-slim AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsndfile1 \
    ffmpeg \
    curl \
    supervisor \
    libpq-dev \
    python3-dev \
    gcc \
    g++ \
    make \
    cmake \
    pkg-config \
    libssl-dev \
    libffi-dev \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and setuptools first
RUN pip install --upgrade pip setuptools wheel

# Set working directory
WORKDIR /app

# Add the project root to the Python path
ENV PYTHONPATH /app

# Copy requirements first for better layer caching
COPY api/requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create config directory and handle credentials securely
RUN mkdir -p /app/api/config
# Create an empty credentials file (will be mounted at runtime)
RUN echo '{}' > /app/api/config/credentials.json && chmod 644 /app/api/config/credentials.json

# Copy the rest of the backend files
COPY api/ ./api/

# Set environment variable for Whisper model directory
ENV WHISPER_CACHE_DIR=/app/models

# Download the Whisper model (simplified, non-blocking)
RUN mkdir -p $WHISPER_CACHE_DIR && \
    (python3 -c "from faster_whisper import WhisperModel; print('Downloading Whisper model...'); WhisperModel('base', download_root='$WHISPER_CACHE_DIR')" || echo "Warning: Failed to download Whisper model, continuing anyway") &

# Copy the frontend build from the first stage
COPY --from=build-step /app/frontend/build ./frontend/build

# Expose the application port
EXPOSE 3000

# Command to start FastAPI from the project root
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]