#!/bin/bash

# Exit on error and print commands
set -e
set -o pipefail

# Configuration
APP_NAME="syntextai"
APP_DIR="/root/app"
DOCKER_COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print status messages
print_status() {
    echo -e "${GREEN}[+] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}"
}

# Function to print warnings
print_warning() {
    echo -e "${YELLOW}[!] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}" >&2
}

# Function to print errors and exit
error_exit() {
    echo -e "${RED}[ERROR] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}" >&2
    exit 1
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
    error_exit "This script must be run as root"
fi

# Install required packages if not exists
install_required_packages() {
    print_status "Checking for required packages..."
    local packages=("docker.io" "docker-compose-plugin" "jq" "curl" "git")
    local missing_packages=()
    
    for pkg in "${packages[@]}"; do
        if ! dpkg -l | grep -q "^ii.*$pkg"; then
            missing_packages+=("$pkg")
        fi
    done
    
    if [ ${#missing_packages[@]} -ne 0 ]; then
        print_status "Installing missing packages: ${missing_packages[*]}"
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends "${missing_packages[@]}" || error_exit "Failed to install required packages"
    fi
}

# Setup Docker environment
setup_docker() {
    print_status "Setting up Docker environment..."
    if ! systemctl is-active --quiet docker; then
        systemctl start docker || error_exit "Failed to start Docker service"
    fi
    systemctl enable docker || print_warning "Failed to enable Docker service"
    usermod -aG docker "$SUDO_USER" || print_warning "Failed to add user to docker group"
}

# Create app directory structure
setup_app_directory() {
    print_status "Setting up application directory..."
    mkdir -p "$APP_DIR" || error_exit "Failed to create app directory"
    
    # Create necessary directories
    mkdir -p "$APP_DIR/data"
    mkdir -p "$APP_DIR/logs"
    
    # Set proper permissions
    chown -R "$SUDO_USER:$SUDO_USER" "$APP_DIR"
    chmod -R 755 "$APP_DIR"
}

# Verify Docker and Docker Compose are available
verify_docker() {
    if ! command_exists docker; then
        error_exit "Docker is not installed. Please install Docker and try again."
    fi
    
    if ! command_exists docker-compose; then
        error_exit "Docker Compose is not installed. Please install Docker Compose and try again."
    fi
    
    if ! docker info > /dev/null 2>&1; then
        error_exit "Docker daemon is not running. Please start Docker and try again."
    fi
}

# Deploy application using Docker Compose
deploy_application() {
    cd "$APP_DIR" || error_exit "Failed to change to app directory"
    
    # Verify .env file exists
    if [ ! -f "$ENV_FILE" ]; then
        error_exit "$ENV_FILE not found in $APP_DIR"
    fi
    
    # Stop and clean up existing containers
    print_status "Stopping and cleaning up existing containers..."
    docker-compose down --remove-orphans || print_warning "No running containers to stop"
    
    # Pull latest images
    print_status "Pulling latest Docker images..."
    docker-compose pull || error_exit "Failed to pull Docker images"
    
    # Start services
    print_status "Starting services..."
    docker-compose up -d --build --remove-orphans || error_exit "Failed to start services"
    
    # Wait for services to be healthy
    print_status "Waiting for services to be healthy..."
    for i in {1..10}; do
        if docker ps --filter "health=healthy" --format '{{.Names}}' | grep -q "syntextai-app"; then
            break
        fi
        if [ $i -eq 10 ]; then
            print_warning "Services are taking too long to start. Continuing anyway..."
            break
        fi
        sleep 5
    done
    
    # Verify services
    print_status "Verifying services..."
    if ! docker-compose ps | grep -q "Up"; then
        error_exit "Some services failed to start"
    fi
    
    # Run database migrations if needed
    run_migrations
}

# Run database migrations
run_migrations() {
    print_status "Running database migrations..."
    if docker-compose exec -T app alembic upgrade head; then
        print_status "Database migrations completed successfully"
    else
        print_warning "Failed to run database migrations. The database might be already up to date."
    fi
}

# Verify deployment
verify_deployment() {
    print_status "Verifying deployment..."
    
    # Check if containers are running
    local running_services
    running_services=$(docker-compose ps --services --filter "status=running")
    
    if [ -z "$running_services" ]; then
        error_exit "No services are running. Deployment failed."
    fi
    
    print_status "Running services:\n$running_services"
    
    # Check application health
    local health_check_url="http://localhost:3000/health"
    local max_retries=10
    local retry_count=0
    
    print_status "Checking application health..."
    while [ $retry_count -lt $max_retries ]; do
        local status_code
        status_code=$(curl -s -o /dev/null -w "%{http_code}" "$health_check_url" || true)
        
        if [ "$status_code" = "200" ]; then
            print_status "Application is healthy!"
            return 0
        fi
        
        retry_count=$((retry_count + 1))
        print_status "Health check attempt $retry_count/$max_retries - Status: ${status_code:-Unable to connect}"
        sleep 5
    done
    
    print_warning "Application health check did not return 200 after $max_retries attempts"
    
    # Show logs for debugging
    print_status "Showing application logs for debugging..."
    docker-compose logs --tail=50 app || true
    
    error_exit "Deployment verification failed. Check the logs above for details."
}

# Main function
main() {
    local start_time
    start_time=$(date +%s)
    
    print_status "🚀 Starting deployment of $APP_NAME..."
    print_status "Working directory: $(pwd)"
    
    # Show system information
    print_status "System information:"
    uname -a
    lsb_release -a 2>/dev/null || true
    
    # Install required packages
    print_status "🔧 Setting up system..."
    install_required_packages
    
    # Setup Docker
    setup_docker
    
    # Verify Docker is working
    verify_docker
    
    # Setup app directory
    setup_app_directory
    
    # Show Docker information
    print_status "🐳 Docker information:"
    docker --version
    docker-compose --version
    docker system info
    
    # Deploy application
    deploy_application
    
    # Verify deployment
    verify_deployment
    
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    print_status "✅ Deployment completed successfully in ${duration} seconds!"
    print_status "🌐 Application is now running at http://localhost:3000"
    
    # Show running containers
    print_status "🐳 Running containers:"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

# Main deployment
main() {
    # Create required directories
    mkdir -p "${APP_DIR}/api/config"
    
    # Set up Firebase credentials
    echo -e "${GREEN}🔑 Setting up Firebase credentials...${NC}"
    cat > "${APP_DIR}/api/config/credentials.json" << EOF
{
  "type": "service_account",
  "project_id": "${FIREBASE_PROJECT_ID}",
  "private_key_id": "${FIREBASE_PRIVATE_KEY_ID}",
  "private_key": "$(echo "${FIREBASE_PRIVATE_KEY}" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')",
  "client_email": "${FIREBASE_CLIENT_EMAIL}",
  "client_id": "${FIREBASE_CLIENT_ID}",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "${FIREBASE_CLIENT_CERT_URL}",
  "universe_domain": "googleapis.com"
}
EOF
    chmod 600 "${APP_DIR}/api/config/credentials.json"

    # Install system dependencies
    install_dependencies
    
    # Deploy the application
    deploy_application

    # Show completion message
    echo -e "\n${GREEN}✅ Deployment complete!${NC}"
    echo -e "\n${GREEN}🌐 Application is now running at:${NC}"
    echo -e "- http://localhost:3000"
    
    # Show running containers
    echo -e "\n${GREEN}🐳 Running containers:${NC}"
    docker ps
}

# Run the main function
main "$@"