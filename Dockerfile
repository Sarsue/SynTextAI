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


FROM base

# Set permissions for log files and directories (ensure directories are writable)
RUN mkdir -p /var/log/syntextai && \
    chown -R root:root /var/log/syntextai && \
    chmod -R 775 /var/log/syntextai

# Expose thc v=e application port
EXPOSE 3000


# Command to start Supervisor (or your application)
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "3000"]