# Stage 1: Build the frontend
FROM node:18-alpine AS build-step

# Define build arguments for environment variables
ARG REACT_APP_FIREBASE_API_KEY
ARG REACT_APP_FIREBASE_AUTH_DOMAIN
ARG REACT_APP_FIREBASE_PROJECT_ID
ARG REACT_APP_FIREBASE_STORAGE_BUCKET
ARG REACT_APP_FIREBASE_MESSAGING_SENDER_ID
ARG REACT_APP_FIREBASE_APP_ID
ARG REACT_APP_STRIPE_API_KEY
ARG REACT_APP_STRIPE_SECRET
ARG REACT_APP_STRIPE_ENDPOINT_SECRET

# Set as environment variables for the build
ENV REACT_APP_FIREBASE_API_KEY=${REACT_APP_FIREBASE_API_KEY}
ENV REACT_APP_FIREBASE_AUTH_DOMAIN=${REACT_APP_FIREBASE_AUTH_DOMAIN}
ENV REACT_APP_FIREBASE_PROJECT_ID=${REACT_APP_FIREBASE_PROJECT_ID}
ENV REACT_APP_FIREBASE_STORAGE_BUCKET=${REACT_APP_FIREBASE_STORAGE_BUCKET}
ENV REACT_APP_FIREBASE_MESSAGING_SENDER_ID=${REACT_APP_FIREBASE_MESSAGING_SENDER_ID}
ENV REACT_APP_FIREBASE_APP_ID=${REACT_APP_FIREBASE_APP_ID}
ENV REACT_APP_STRIPE_API_KEY=${REACT_APP_STRIPE_API_KEY}
ENV REACT_APP_STRIPE_SECRET=${REACT_APP_STRIPE_SECRET}
ENV REACT_APP_STRIPE_ENDPOINT_SECRET=${REACT_APP_STRIPE_ENDPOINT_SECRET}

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

# Define build arguments for Firebase credentials
ARG FIREBASE_PROJECT_ID
ARG FIREBASE_PRIVATE_KEY
ARG FIREBASE_CLIENT_EMAIL
ARG FIREBASE_PRIVATE_KEY_ID
ARG FIREBASE_CLIENT_ID
ARG FIREBASE_CLIENT_CERT_URL
ARG FIREBASE_AUTH_URI
ARG FIREBASE_TOKEN_URI
ARG FIREBASE_AUTH_PROVIDER_CERT_URL

# Set as environment variables
ENV FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}
ENV FIREBASE_PRIVATE_KEY=${FIREBASE_PRIVATE_KEY}
ENV FIREBASE_CLIENT_EMAIL=${FIREBASE_CLIENT_EMAIL}
ENV FIREBASE_PRIVATE_KEY_ID=${FIREBASE_PRIVATE_KEY_ID}
ENV FIREBASE_CLIENT_ID=${FIREBASE_CLIENT_ID}
ENV FIREBASE_CLIENT_CERT_URL=${FIREBASE_CLIENT_CERT_URL}
ENV FIREBASE_AUTH_URI=${FIREBASE_AUTH_URI:-https://accounts.google.com/o/oauth2/auth}
ENV FIREBASE_TOKEN_URI=${FIREBASE_TOKEN_URI:-https://oauth2.googleapis.com/token}
ENV FIREBASE_AUTH_PROVIDER_CERT_URL=${FIREBASE_AUTH_PROVIDER_CERT_URL:-https://www.googleapis.com/oauth2/v1/certs}

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsndfile1 \
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