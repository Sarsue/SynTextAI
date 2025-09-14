#!/bin/bash
set -e

# -------------------------------
# Configuration
# -------------------------------
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"

# Docker Compose file location
DOCKER_COMPOSE_FILE="$APP_DIR/docker-compose.yml"

# -------------------------------
# Step 1: Install dependencies
# -------------------------------
echo "🚀 Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io curl ufw

# Enable and start Docker
sudo systemctl enable --now docker
if ! sudo systemctl is-active --quiet docker; then
    echo "❌ Docker failed to start. Exiting."
    exit 1
fi

# -------------------------------
# Step 2: Install Docker Compose
# -------------------------------
if ! command -v docker-compose >/dev/null 2>&1; then
    echo "📦 Installing Docker Compose..."
    DOCKER_COMPOSE_VERSION="v2.28.1"
    sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# -------------------------------
# Step 3: Prepare app directory
# -------------------------------
echo "📂 Setting up app directory..."
mkdir -p $APP_DIR
cp -r ./* $APP_DIR/

# -------------------------------
# Step 4: Setup firewall
# -------------------------------
echo "🔥 Configuring firewall..."
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw --force enable

# -------------------------------
# Step 5: Launch Docker Compose
# -------------------------------
echo "🐳 Starting Docker Compose stack..."
cd $APP_DIR

# Pull latest images and start containers
sudo docker-compose -f $DOCKER_COMPOSE_FILE pull
sudo docker-compose -f $DOCKER_COMPOSE_FILE up -d --remove-orphans --build

# -------------------------------
# Step 6: Wait for SSL certificates
# -------------------------------
echo "🔐 Waiting for Let's Encrypt certificates..."
echo "ℹ️ First-time deployment may take 30–60 seconds to generate SSL certificates."
sleep 30  # give acme-companion time to request certificates

# -------------------------------
# Step 7: Deployment complete
# -------------------------------
echo "✅ Deployment complete!"
echo "🌐 Main app: https://$DOMAIN"
echo "🔍 SearxNG search: https://search.$DOMAIN"
