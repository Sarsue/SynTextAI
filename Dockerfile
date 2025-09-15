# Stage 1: Build the frontend
FROM node:18-alpine AS build-step

# Set build-time environment variables
ENV PUBLIC_URL=/
ENV NODE_ENV=production
ENV REACT_APP_API_URL=/api

WORKDIR /app/frontend

# Copy package files and install dependencies
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --only=production

# Copy and build the application
COPY frontend/ ./
RUN npm run build

# Stage 2: Set up the Python backend
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    PORT=3000 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies in a single layer
RUN set -ex && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1 \
        ffmpeg \
        curl \
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

# Set working directory
WORKDIR /app

# Install Python dependencies first to leverage Docker cache
COPY api/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p /app/api/config /app/frontend/build && \
    useradd -m appuser && \
    chown -R appuser:appuser /app

# Copy application code
COPY --chown=appuser:appuser api/ ./api/

# Copy frontend build from build stage
COPY --from=build-step --chown=appuser:appuser /app/frontend/build ./frontend/build

# Switch to non-root user
USER appuser

# Set environment variable for Whisper model directory
ENV WHISPER_CACHE_DIR=/home/appuser/.cache/whisper

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

# Command to start the application
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000", "--workers", "4"]