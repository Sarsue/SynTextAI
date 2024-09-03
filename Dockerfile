# Stage 1: Build the React frontend
FROM node:18-alpine AS build-step
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --only=production
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# Stage 2: Set up the Python backend with the built frontend
FROM python:3.10-slim
WORKDIR /app

# Copy the build artifacts from the first stage
COPY --from=build-step /app/build ./build

# Copy backend files
COPY api/ ./api/

# Install Python dependencies
RUN pip install --no-cache-dir -r ./api/requirements.txt && \
    pip install --no-cache-dir supervisor

# Create the log directory for supervisor
RUN mkdir -p /var/log/supervisor

# Copy the supervisord configuration file
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose the application port
EXPOSE 3000

# Start Supervisor
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
