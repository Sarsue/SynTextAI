#!/bin/bash
set -e

# Configuration
APP_DIR="/home/root/app"
ENV_FILE="$APP_DIR/frontend/.env"
CREDENTIALS_DIR="$APP_DIR/api/config"
CREDENTIALS_FILE="$CREDENTIALS_DIR/credentials.json"

echo "🚀 Starting deployment process..."

# Create necessary directories
echo "📂 Setting up directories..."
sudo mkdir -p "$APP_DIR"
sudo mkdir -p "$CREDENTIALS_DIR"
sudo mkdir -p "$APP_DIR/frontend"

# Set proper ownership
echo "🔒 Setting permissions..."
sudo chown -R $USER:$USER "$APP_DIR"
sudo chmod -R 755 "$APP_DIR"

# Install system dependencies
echo "📦 Installing dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io curl ufw

# Install Docker Compose if missing
if ! command -v docker-compose >/dev/null 2>&1; then
    echo "📦 Installing Docker Compose..."
    DOCKER_COMPOSE_VERSION="v2.28.1"
    sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# Start Docker
echo "🐳 Starting Docker..."
sudo systemctl enable --now docker
if ! sudo systemctl is-active --quiet docker; then
    echo "❌ Docker failed to start"
    exit 1
fi

# Change to app directory
cd "$APP_DIR"

# Stop existing containers
echo "🛑 Stopping existing project containers..."
docker-compose down --remove-orphans || true

# Cleanup unused Docker resources
echo "🧹 Cleaning up unused Docker resources..."
docker system prune -af --volumes || true

# Pull latest images
echo "🐳 Pulling latest images..."
docker-compose pull

# Start containers
echo "🚀 Starting containers..."
docker-compose up -d --remove-orphans --build --force-recreate

# Verify containers are running
if ! docker-compose ps | grep -q "Up"; then
    echo "⚠️ Some containers failed to start. Attempting to restart..."
    docker-compose down
    docker-compose up -d
fi

echo "✅ Deployment complete! SynTextAI is live, with SSL managed automatically by nginx-proxy + acme-companion."
