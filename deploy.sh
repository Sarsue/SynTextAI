#!/bin/bash
set -e

APP_DIR="/home/root/app"

echo "🚀 Starting deployment process..."

# 1️⃣ Install system dependencies
echo "📦 Installing dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io curl ufw

# 2️⃣ Install Docker Compose if missing
if ! command -v docker-compose >/dev/null 2>&1; then
    echo "📦 Installing Docker Compose..."
    DOCKER_COMPOSE_VERSION="v2.28.1"
    sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# 3️⃣ Start Docker
sudo systemctl enable --now docker
if ! sudo systemctl is-active --quiet docker; then
    echo "❌ Docker failed to start"
    exit 1
fi

# 4️⃣ Ensure app directory exists
mkdir -p $APP_DIR
cd $APP_DIR

# 5️⃣ Nuclear cleanup of Docker resources
echo "💣 Performing full Docker cleanup..."

# Stop and remove all containers
docker ps -a -q | xargs -r docker rm -f

# Remove all images
docker images -a -q | xargs -r docker rmi -f

# Remove all volumes
docker volume ls -q | xargs -r docker volume rm -f

# Remove all networks not used by default
docker network ls -q | xargs -r docker network rm || true

# 6️⃣ Pull latest images and start containers
echo "🐳 Pulling latest images and starting containers..."
docker-compose pull
docker-compose up -d --remove-orphans --build --force-recreate

echo "✅ Deployment complete! All old containers/images/volumes are cleared, your site is live, and SSL should be automatically handled by nginx-proxy + acme-companion."
