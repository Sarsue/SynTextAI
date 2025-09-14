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

# Load environment variables from .env file safely
if [ -f ".env" ]; then
    echo -e "${GREEN}🔧 Loading environment variables...${NC}"
    # Use set -a to automatically export all variables
    set -a
    # Source the .env file directly
    . ./.env
    set +a
    echo -e "${GREEN}✅ Environment variables loaded${NC}"
else
    echo -e "${YELLOW}⚠️ Warning: .env file not found${NC}"
fi

# Stop and remove any running containers
echo -e "${GREEN}🛑 Stopping and removing any running containers...${NC}"
if [ "$(docker ps -aq)" ]; then
    docker stop $(docker ps -aq) || true
    docker rm -f $(docker ps -aq) || true
fi

# Remove any existing networks
echo -e "${GREEN}🧹 Removing any existing networks...${NC}"
if [ "$(docker network ls -q -f name=syntextai)" ]; then
    docker network rm $(docker network ls -q -f name=syntextai) || true
fi

# Clean up Docker resources
echo -e "${GREEN}🧹 Cleaning up Docker resources...${NC}"
docker system prune -af --volumes --filter "label!=com.docker.compose.project=syntextai" || true

# Pull latest images
echo -e "${GREEN}🐳 Pulling latest images...${NC}"
docker-compose pull || error_exit "Failed to pull Docker images"

# Start containers with retry logic
MAX_RETRIES=3
RETRY_DELAY=10

start_containers() {
    echo -e "${GREEN}🚀 Starting containers (Attempt $1/$MAX_RETRIES)...${NC}"
    docker-compose up -d --remove-orphans --build --force-recreate
    return $?
}

# Try starting containers with retries
for ((i=1; i<=$MAX_RETRIES; i++)); do
    if start_containers $i; then
        echo -e "${GREEN}✅ Containers started successfully!${NC}"
        break
    else
        if [ $i -eq $MAX_RETRIES ]; then
            error_exit "Failed to start containers after $MAX_RETRIES attempts"
        fi
        echo -e "${YELLOW}⚠️ Attempt $i failed, retrying in $RETRY_DELAY seconds...${NC}"
        sleep $RETRY_DELAY
    fi
done

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
