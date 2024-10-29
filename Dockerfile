# Stage 1: Build the React frontend
FROM node:18-alpine AS build-step
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --only=production
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# Stage 2: Set up the Python backend with FFmpeg and Whisper
FROM python:3.10-slim

# Install FFmpeg and system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create a non-root user
RUN adduser --disabled-password myuser

# Copy build artifacts from the first stage
COPY --from=build-step /app/build ./build

# Copy backend files
COPY api/ ./api/

# Install Python dependencies
RUN pip install --no-cache-dir -r ./api/requirements.txt && \
    pip install --no-cache-dir supervisor

# Create log directory and set permissions
RUN mkdir -p /var/log/supervisor && chown myuser:myuser /var/log/supervisor

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 3000

# Switch to non-root user
USER myuser

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
