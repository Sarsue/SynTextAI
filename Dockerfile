# Stage 1: Build the React frontend
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
FROM python:3.10-slim

# Install FFmpeg, system dependencies, and other tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    supervisor && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create a non-root user (optional step, we can change this later if we prefer to run as root)
RUN useradd -ms /bin/bash nonrootuser

WORKDIR /app

# Copy only the build artifacts from the first stage (frontend build step), not node_modules
COPY --from=build-step /app/build ./build

# Copy backend files (api directory)
COPY api/ ./api/

# Copy the db folder into the container (make sure db is in the same directory as the Dockerfile)
COPY db/ /app/db/

# Install Python dependencies and remove cache
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Install Celery and Supervisor
RUN pip install --no-cache-dir celery && \
    pip install --no-cache-dir supervisor

# Set permissions for log files and directories (ensure directories are writable)
RUN mkdir -p /var/log/supervisor && \
    chown -R root:root /var/log/supervisor && \
    chmod -R 775 /var/log/supervisor

# Expose the application port
EXPOSE 3000

# Supervisor Configuration
COPY supervisord.conf /etc/supervisor/supervisord.conf

# Run as root (you can remove the useradd command if you want to run as root directly)
USER root

# Command to run Supervisor, which will manage Gunicorn and Celery
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
