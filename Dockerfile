# Stage 1: Build the React frontend
FROM node:18-alpine AS build-step

# Set working directory
WORKDIR /app

# Copy only package files first to leverage caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies
RUN npm ci --only=production && npm prune --production

# Copy the remaining files and build the app
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

# Set working directory
WORKDIR /app

# Copy the React build artifacts from the first stage
COPY --from=build-step /app/build ./frontend/build

# Copy backend files
COPY api/ ./api/

# Install Python dependencies
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Set permissions for log files and directories
RUN mkdir -p /var/log/syntextai && \
    chmod -R 775 /var/log/syntextai

# Expose the application port
EXPOSE 3000

# Supervisor configuration
COPY supervisord.conf /etc/supervisor/supervisord.conf

# Command to start Supervisor
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
