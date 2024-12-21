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

# Copy the db folder into the container (make sure db is in the same directory as the Dockerfile)
COPY db/ /app/db/

# Install Python dependencies and remove cache
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Install Celery and Supervisor
RUN pip install --no-cache-dir celery && \
    pip install --no-cache-dir supervisor

# Stage 3: Download Litestream binary
FROM base AS litestream-download
RUN apt-get update && \
    apt-get install -y --no-install-recommends wget && \
    wget https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz -O /tmp/litestream.tar.gz && \
    tar -xz -C /tmp -f /tmp/litestream.tar.gz && \
    mv /tmp/litestream /usr/local/bin/litestream && \
    rm /tmp/litestream.tar.gz && \
    apt-get remove -y wget && \
    apt-get autoremove -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Final stage: Combine everything
FROM base

# Copy Google Cloud credentials
COPY api/config/credentials.json /app/service-account-key.json

# Set Google Cloud credentials environment variable
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/service-account-key.json

# Set permissions for log files and directories (ensure directories are writable)
RUN mkdir -p /var/log/syntextai && \
    chown -R root:root /var/log/syntextai && \
    chmod -R 775 /var/log/syntextai

# Copy Litestream binary from the download stage
COPY --from=litestream-download /usr/local/bin/litestream /usr/local/bin/litestream

# Expose the application port
EXPOSE 3000

# Supervisor Configuration
COPY supervisord.conf /etc/supervisor/supervisord.conf

# Copy Litestream configuration
COPY litestream.yml /etc/litestream.yml

# Run as root (you can remove the useradd command if you want to run as root directly)
USER root
# Copy the entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

# Set the entrypoint to the script
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Command to start Supervisor (or your application)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
