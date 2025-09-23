
# ==========================
# Stage 1: Build the frontend
# ==========================
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

# Set build-time env vars
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

# Copy package files first to leverage caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies
RUN npm ci

# Copy remaining files and build
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# ==========================
# Stage 2: Python backend
# ==========================
FROM python:3.10-slim AS base

# Install system dependencies and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app
ENV PYTHONPATH /app

# Define Firebase build args
ARG FIREBASE_PROJECT_ID
ARG FIREBASE_PRIVATE_KEY
ARG FIREBASE_CLIENT_EMAIL
ARG FIREBASE_PRIVATE_KEY_ID
ARG FIREBASE_CLIENT_ID
ARG FIREBASE_CLIENT_CERT_URL
ARG FIREBASE_AUTH_URI
ARG FIREBASE_TOKEN_URI
ARG FIREBASE_AUTH_PROVIDER_CERT_URL

# Set Firebase environment variables
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
    ffmpeg \
    libsndfile1 \
    curl \
    supervisor \
    ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy backend code and CA certificate
COPY api/ ./api/
COPY api/config/ca-certificate.crt ./api/config/ca-certificate.crt

# Environment variable for PostgreSQL CA cert
ENV DATABASE_SSLROOTCERT=/app/api/config/ca-certificate.crt

# Install Python dependencies
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Whisper setup
ENV WHISPER_CACHE_DIR=/app/models
RUN mkdir -p $WHISPER_CACHE_DIR && \
    pip install faster-whisper && \
    python -c "from faster_whisper import WhisperModel; WhisperModel('base', download_root='$WHISPER_CACHE_DIR')"

# Copy frontend build from first stage
COPY --from=build-step /app/frontend/build ./frontend/build

# Expose app port
EXPOSE 3000

# Start FastAPI
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]
