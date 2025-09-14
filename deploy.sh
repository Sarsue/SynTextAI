#!/bin/bash
set -e

# Configuration
APP_DIR="/home/root/app"
ENV_FILE="$APP_DIR/frontend/.env"
CREDENTIALS_DIR="$APP_DIR/api/config"
CREDENTIALS_FILE="$CREDENTIALS_DIR/credentials.json"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Starting SynTextAI deployment...${NC}"

# Function to log errors and exit
error_exit() {
    echo -e "${RED}❌ $1${NC}" >&2
    exit 1
}

# Function to check if a command exists
command_exists() {
    command -v "$@" >/dev/null 2>&1
}

# Create necessary directories
echo -e "${GREEN}📂 Setting up directories...${NC}"
sudo mkdir -p "$APP_DIR" "$CREDENTIALS_DIR" "$APP_DIR/frontend" || error_exit "Failed to create directories"

# Set proper ownership
echo -e "${GREEN}🔒 Setting permissions...${NC}"
sudo chown -R $USER:$USER "$APP_DIR" || error_exit "Failed to set ownership"
sudo chmod -R 755 "$APP_DIR" || error_exit "Failed to set permissions"

# Install system dependencies
echo -e "${GREEN}📦 Installing system dependencies...${NC}"
sudo apt-get update && sudo apt-get install -y docker.io curl ufw || error_exit "Failed to install dependencies"

# Install Docker Compose if missing
if ! command_exists docker-compose; then
    echo -e "${GREEN}📦 Installing Docker Compose...${NC}"
    DOCKER_COMPOSE_VERSION="v2.28.1"
    sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose || \
        error_exit "Failed to download Docker Compose"
    sudo chmod +x /usr/local/bin/docker-compose || error_exit "Failed to make Docker Compose executable"
fi

# Start and enable Docker
echo -e "${GREEN}🐳 Starting Docker...${NC}"
sudo systemctl enable --now docker || error_exit "Failed to start Docker"
if ! sudo systemctl is-active --quiet docker; then
    error_exit "Docker failed to start"
fi

# Change to app directory
cd "$APP_DIR" || error_exit "Failed to change to app directory"

# Stop and clean up existing containers
echo -e "${GREEN}🛑 Stopping existing project containers...${NC}"
docker-compose down --remove-orphans || echo -e "${YELLOW}⚠️ No existing containers to stop${NC}"

# Cleanup unused Docker resources
echo -e "${GREEN}🧹 Cleaning up unused Docker resources...${NC}"
docker system prune -af --volumes || echo -e "${YELLOW}⚠️ Docker cleanup failed but continuing...${NC}"

# Pull latest images
echo -e "${GREEN}🐳 Pulling latest images...${NC}"
docker-compose pull || error_exit "Failed to pull Docker images"

# Start containers
echo -e "${GREEN}🚀 Starting containers...${NC}"
docker-compose up -d --remove-orphans --build --force-recreate || error_exit "Failed to start containers"

# Function to check container health
check_container_health() {
    local container_name=$1
    local max_attempts=30
    local attempt=0

    echo -e "${GREEN}🔄 Waiting for $container_name to be healthy...${NC}"
    
    while [ $attempt -lt $max_attempts ]; do
        local health_status
        health_status=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "starting")
        
        if [ "$health_status" = "healthy" ]; then
            echo -e "${GREEN}✅ $container_name is healthy!${NC}"
            return 0
        elif [ "$health_status" = "unhealthy" ]; then
            echo -e "${RED}❌ $container_name is unhealthy!${NC}"
            docker logs "$container_name"
            return 1
        fi
        
        attempt=$((attempt + 1))
        echo -e "${YELLOW}⏳ Waiting for $container_name to be healthy... (attempt $attempt/$max_attempts)${NC}"
        sleep 10
    done
    
    echo -e "${RED}❌ Timed out waiting for $container_name to be healthy${NC}"
    docker logs "$container_name"
    return 1
}

# Check health of critical services
check_container_health "syntextai-app"
check_container_health "syntextai-db"
check_container_health "syntextai-redis"

# Verify all containers are running
if ! docker-compose ps | grep -q "Up"; then
    echo -e "${YELLOW}⚠️ Some containers failed to start. Attempting to restart...${NC}"
    docker-compose down
    docker-compose up -d || error_exit "Failed to restart containers"
    
    # Give containers some time to start
    sleep 30
    
    if ! docker-compose ps | grep -q "Up"; then
        error_exit "Failed to start containers after retry"
    fi
fi

echo -e "${GREEN}✅ Deployment complete! SynTextAI is live!${NC}"
echo -e "${GREEN}🌐 Access your application at: https://syntextai.com${NC}"
echo -e "${GREEN}🔍 Search functionality available at: https://search.syntextai.com${NC}"

# Show container status
echo -e "\n${GREEN}📊 Container Status:${NC}"
docker-compose ps
