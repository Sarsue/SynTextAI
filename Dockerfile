# Stage 2: Set up the Python backend with FFmpeg, Whisper, and Litestream
FROM python:3.10-slim

# Install FFmpeg, system dependencies, Litestream, and other tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Litestream (download the latest release)
RUN curl -fsSL https://github.com/benbjohnson/litestream/releases/download/v0.2.7/litestream-v0.2.7-linux-amd64.tar.gz | tar -xz -C /usr/local/bin

WORKDIR /app

# Copy only the build artifacts from the first stage, not node_modules
COPY --from=build-step /app/build ./build

# Copy backend files
COPY api/ ./api/

# Set the GOOGLE_APPLICATION_CREDENTIALS environment variable
# This points to the credentials.json file inside the 'api' directory in the container
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/api/config/credentials.json

# Install Python dependencies and remove cache
RUN pip install --no-cache-dir -r ./api/requirements.txt && \
    pip install --no-cache-dir supervisor

# Create the log directory for supervisor
RUN mkdir -p /var/log/supervisor

# Copy the supervisord configuration file
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose the application port
EXPOSE 3000

# Install Litestream configuration
COPY litestream.yml /etc/litestream.yml

# Start Supervisor
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
