# Stage 1: Base image with system dependencies
FROM python:3.10-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    libsndfile1 \
    git \
    libpq-dev \
    python3-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1-mesa-glx \
    libsndfile1-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libtiff5-dev \
    libjpeg-dev \
    libpng-dev \
    libwebp-dev \
    libopenblas-dev \
    liblapack-dev \
    gfortran \
    postgresql-server-dev-all \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    VENV_PATH="/app/.venv" \
    WHISPER_CACHE_DIR=/root/.cache/whisper

# Install Python dependencies in multiple steps for better caching
COPY api/requirements.txt .

# Install pip-tools first
RUN pip install --upgrade pip && \
    pip install pip-tools

# Install Python dependencies with retries
RUN pip install --no-cache-dir -r requirements.txt

# Download the Whisper model
RUN mkdir -p "$WHISPER_CACHE_DIR" && \
    python -c "from faster_whisper import WhisperModel; WhisperModel('base', download_root='$WHISPER_CACHE_DIR')"

# Copy setup files and install the package in development mode
COPY setup.py .
COPY setup_paths.py .
COPY api/ ./api/
RUN pip install -e .

# Set environment variables for production
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    DEBIAN_FRONTEND=noninteractive

# Stage 2: Frontend build
FROM node:18-alpine AS frontend-builder

# Set working directory
WORKDIR /app/frontend

# Copy package files first for better caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies
RUN npm ci --prefer-offline --no-audit --progress=false

# Copy remaining frontend files and build
COPY frontend/ ./
RUN npm run build && \
    npm cache clean --force && \
    rm -rf node_modules

# Final stage
FROM base

# Copy frontend build
COPY --from=frontend-builder /app/frontend/build ./frontend/build

# Set environment variables for Firebase (will be overridden by docker-compose if needed)
ENV FIREBASE_AUTH_URI=https://accounts.google.com/o/oauth2/auth \
    FIREBASE_TOKEN_URI=https://oauth2.googleapis.com/token \
    FIREBASE_AUTH_PROVIDER_CERT_URL=https://www.googleapis.com/oauth2/v1/certs

# Expose the application port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

# Command to run the application
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]