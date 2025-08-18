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
    libgl1 \
    libglib2.0-0 \
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
    libgomp1 \
    libatomic1 \
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

# Install faster-whisper with specific version
RUN pip install --no-cache-dir faster-whisper==0.9.0

# Create cache directory with correct permissions
RUN mkdir -p "$WHISPER_CACHE_DIR" && chmod 777 "$WHISPER_CACHE_DIR"

# Copy the application code
COPY api/ ./api/

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

# Set Python path to include the app directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Command to run the application
WORKDIR /app
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]