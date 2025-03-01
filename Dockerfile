# Stage 1: Frontend build
FROM node:18-alpine AS build-step
WORKDIR /app
RUN echo "nameserver 8.8.8.8" > /etc/resolv.conf
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --only=production && npm prune --production
COPY frontend/ ./
RUN npm run build && npm cache clean --force

# Stage 2: Backend
FROM python:3.10-slim AS base
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    supervisor \
    dnsutils && \  # Add dnsutils for nslookup
    apt-get clean && rm -rf /var/lib/apt/lists/*
RUN echo "nameserver 8.8.8.8" > /etc/resolv.conf
WORKDIR /app
COPY --from=build-step /app/build ./build
COPY api/ ./api/
COPY api/config/ca-certificate.crt ./api/config/ca-certificate.crt
COPY api/.env ./api/.env
RUN pip install --no-cache-dir -r ./api/requirements.txt

# Final Stage
FROM base
RUN echo "nameserver 8.8.8.8" > /etc/resolv.conf && \
    echo "nameserver 8.8.4.4" >> /etc/resolv.conf  # Add secondary DNS
EXPOSE 3000
COPY supervisord.conf /etc/supervisor/supervisord.conf

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]