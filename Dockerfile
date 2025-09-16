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
RUN npm ci --prefer-offline --no-audit --progress=false

# Copy the remaining files and build
COPY frontend/ ./
RUN npm run build && \
    npm cache clean --force && \
    rm -rf /root/.npm /tmp/*

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
    apt-get update && \
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

# Create a script to download the model at runtime
RUN echo '#!/bin/bash\
set -e\
\
WHISPER_CACHE_DIR=${WHISPER_CACHE_DIR:-/app/models}\
MODEL_PATH="$WHISPER_CACHE_DIR/faster-whisper-base"\
\
echo "📦 Checking for Whisper model in $MODEL_PATH"\
\
if [ -d "$MODEL_PATH" ]; then\
    echo "✅ Found existing Whisper model"\
    exit 0\
fi\
\
# Create the directory if it doesn\'t exist\
mkdir -p "$WHISPER_CACHE_DIR"\
\
# Download the model with retries\
for i in {1..3}; do\
    echo "Attempt $i/3: Downloading Whisper model..."\
    if python3 -c "\
import os\
from faster_whisper import download_model\
print(\"Starting model download...\")\n\
try:\n    download_model(\"base\", output_dir=\"$WHISPER_CACHE_DIR\", local_files_only=False)\n    print(\"✅ Successfully downloaded Whisper model\")\n    import sys\n    sys.exit(0)\nexcept Exception as e:\n    print(f\"❌ Download failed: {str(e)}\")\n    import sys\n    sys.exit(1)\
"; then\
        echo "✅ Successfully downloaded Whisper model"\
        exit 0\
    fi\
    \
    if [ $i -lt 3 ]; then\
        sleep $((i * 5))\
    fi\ndone\n\
echo "❌ Failed to download Whisper model after 3 attempts"\nexit 1\
' > /usr/local/bin/download-whisper-model && \
    chmod +x /usr/local/bin/download-whisper-model

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

# Create a startup script that ensures the model is downloaded before starting the app
RUN echo '#!/bin/bash\
set -e\
\
# Download the model if it doesn\'t exist\nif ! /usr/local/bin/download-whisper-model; then\
    echo "⚠️ Warning: Failed to download Whisper model. The app will start but may not function correctly."\
fi\n\n# Start the application\nexec "$@"\n' > /usr/local/bin/entrypoint.sh && \
    chmod +x /usr/local/bin/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Command to start FastAPI from the project root
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]