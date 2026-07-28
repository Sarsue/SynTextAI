# Stage 1: Build the frontend
FROM node:20-alpine AS build-step

# Define build arguments for environment variables
# (Vite only exposes vars prefixed VITE_ to client code — see vite.config.ts)
ARG VITE_FIREBASE_API_KEY
ARG VITE_FIREBASE_AUTH_DOMAIN
ARG VITE_FIREBASE_PROJECT_ID
ARG VITE_FIREBASE_STORAGE_BUCKET
ARG VITE_FIREBASE_MESSAGING_SENDER_ID
ARG VITE_FIREBASE_APP_ID
ARG VITE_STRIPE_API_KEY
ARG VITE_POST_HOG_API_KEY

# Set as environment variables for the build
ENV VITE_FIREBASE_API_KEY=${VITE_FIREBASE_API_KEY}
ENV VITE_FIREBASE_AUTH_DOMAIN=${VITE_FIREBASE_AUTH_DOMAIN}
ENV VITE_FIREBASE_PROJECT_ID=${VITE_FIREBASE_PROJECT_ID}
ENV VITE_FIREBASE_STORAGE_BUCKET=${VITE_FIREBASE_STORAGE_BUCKET}
ENV VITE_FIREBASE_MESSAGING_SENDER_ID=${VITE_FIREBASE_MESSAGING_SENDER_ID}
ENV VITE_FIREBASE_APP_ID=${VITE_FIREBASE_APP_ID}
ENV VITE_STRIPE_API_KEY=${VITE_STRIPE_API_KEY}
ENV VITE_POST_HOG_API_KEY=${VITE_POST_HOG_API_KEY}

WORKDIR /app/frontend

# Copy only package files first to leverage caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies (including dev dependencies needed for build)
RUN npm ci --prefer-offline --no-audit --progress=false

# Copy the remaining files and build
COPY frontend/ ./
RUN npm run build && \
    npm cache clean --force && \
    rm -rf /root/.npm /tmp/*

# Stage 2: Set up the Python backend with FFmpeg, Whisper, and dependencies
FROM python:3.10-slim-bookworm AS base

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

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_VERSION=1.5.1 \
    WHISPER_CACHE_DIR=/app/models

# Install system dependencies
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update && \
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
    ca-certificates \
    libssl-dev \
    libffi-dev \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Set working directory
WORKDIR /app

# Add the project root to the Python path
ENV PYTHONPATH /app

# Copy requirements first for better layer caching
COPY api/requirements.txt ./

# Install Python dependencies with retry logic
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend files
COPY api/ ./api/

# Create model directory
RUN mkdir -p $WHISPER_CACHE_DIR && chmod 777 $WHISPER_CACHE_DIR

# Install the script that downloads the model at runtime (kept as a real
# file under api/scripts/ rather than generated via RUN echo -- a Dockerfile
# line-continued echo strips newlines and previously produced a corrupted,
# unexecutable script).
COPY api/scripts/download-whisper-model.sh /usr/local/bin/download-whisper-model
RUN chmod +x /usr/local/bin/download-whisper-model && \
    test -x /usr/local/bin/download-whisper-model

# Copy the frontend build from the first stage
COPY --from=build-step /app/frontend/build ./frontend/build

# Clean up
RUN find /usr/local -depth \
    \( \
        -type d -a -name "__pycache__" -o \
        -type f -a -name "*.pyc" -o \
        -type f -a -name "*.pyo" \
    \) -exec rm -rf '{}' + && \
    rm -rf /tmp/*

# Expose the application port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

# Create a simple entrypoint script
RUN echo '#!/bin/sh\n\nif [ "${SKIP_WHISPER_DOWNLOAD:-0}" = "1" ]; then\n    echo "⚠️ Warning: SKIP_WHISPER_DOWNLOAD=1 set. Proceeding without downloading Whisper model."\nelif [ -f "/usr/local/bin/download-whisper-model" ]; then\n    if ! /usr/local/bin/download-whisper-model; then\n        echo "⚠️ Warning: Failed to download Whisper model. The app will start but may not function correctly."\n    fi\nelse\n    echo "⚠️ Warning: download-whisper-model not found. The app will start but may not function correctly."\nfi\n\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Command to start FastAPI from the project root
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]