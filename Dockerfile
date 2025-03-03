# Stage 1: Build the frontend
FROM node:18-alpine AS build-step
WORKDIR /app

# Copy only package files first to leverage caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies
RUN npm ci --only=production && npm prune --production

# Copy the remaining files and build
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# Stage 2: Set up the Python backend with FFmpeg and Whisper
FROM python:3.10-slim AS base

# Install FFmpeg, system dependencies, and other tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    supervisor && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the build artifacts from the first stage (frontend build step), not node_modules
COPY --from=build-step /app/build ./build
# Copy backend files (api directory)
COPY api/ ./api/

# Install Python dependencies and remove cache
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Set PYTHONPATH to include the api directory
ENV PYTHONPATH=/app/api

# Set the working directory to /app/api
WORKDIR /app/api

# Expose the application port
EXPOSE 3000

# Command to start the FastAPI application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]