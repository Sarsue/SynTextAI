# Stage 1: Frontend build
FROM node:18-alpine AS build-step
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --only=production && npm prune --production
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# Stage 2: Backend
FROM python:3.10-slim AS base
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 curl supervisor && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY api/ ./api/
RUN pip install --no-cache-dir -r ./api/requirements.txt
EXPOSE 3000
COPY supervisord.conf /etc/supervisor/supervisord.conf
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

