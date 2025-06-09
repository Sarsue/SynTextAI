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

# Set as environment variables for the build
ENV REACT_APP_FIREBASE_API_KEY=${REACT_APP_FIREBASE_API_KEY}
ENV REACT_APP_FIREBASE_AUTH_DOMAIN=${REACT_APP_FIREBASE_AUTH_DOMAIN}
ENV REACT_APP_FIREBASE_PROJECT_ID=${REACT_APP_FIREBASE_PROJECT_ID}
ENV REACT_APP_FIREBASE_STORAGE_BUCKET=${REACT_APP_FIREBASE_STORAGE_BUCKET}
ENV REACT_APP_FIREBASE_MESSAGING_SENDER_ID=${REACT_APP_FIREBASE_MESSAGING_SENDER_ID}
ENV REACT_APP_FIREBASE_APP_ID=${REACT_APP_FIREBASE_APP_ID}
ENV REACT_APP_STRIPE_API_KEY=${REACT_APP_STRIPE_API_KEY}

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