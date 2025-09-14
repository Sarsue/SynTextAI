#!/bin/bash
set -e

APP_NAME="syntextai"
APP_DIR="/home/root/app"
DOCKER_COMPOSE_FILE="docker-compose.yml"
DOCKER_IMAGE="osasdeeon/syntextai:latest"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

status() { echo -e "${GREEN}[+]${NC} $1"; }
warning() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[!] ERROR:${NC} $1"; exit 1; }

cd "$APP_DIR" || cd ~

DOCKER_COMPOSE_CMD="docker compose"
if command -v docker-compose &> /dev/null; then
  DOCKER_COMPOSE_CMD="docker-compose"
fi

# Fallback mode only stops and restarts containers
if [ "$1" == "fallback" ]; then
  $DOCKER_COMPOSE_CMD down || true
  $DOCKER_COMPOSE_CMD up -d --force-recreate
  exit 0
fi

status "Pulling latest Docker image..."
docker pull "$DOCKER_IMAGE" || warning "Failed to pull image, using local"

status "Starting zero-downtime deployment..."
# Start new containers without stopping old ones
$DOCKER_COMPOSE_CMD up -d --pull always --build --remove-orphans

status "Waiting for containers to be healthy..."
for i in {1..30}; do
  HEALTH=$(curl -s http://localhost:3000/health || true)
  if [[ "$HEALTH" == *"OK"* ]]; then
    status "✅ Application is healthy"
    break
  fi
  sleep 5
done

# Run DB migrations safely
if docker ps | grep -q "${APP_NAME}_api"; then
  status "Running database migrations..."
  docker exec -it ${APP_NAME}_api_1 alembic upgrade head || warning "Migrations failed"
fi

status "Deployment complete!"