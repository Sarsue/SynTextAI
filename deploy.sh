#!/bin/bash
set -e

# Colors for better output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"

# Function to run commands with error handling
run_command() {
    log_info "Running: $*"
    "$@" || log_error "Command failed: $*"
}

# Function to install required packages
install_required_packages() {
    log_info "Installing required system packages..."
    
    # Update package lists
    run_command sudo apt-get update
    
    # Install Docker Compose plugin if not present
    if ! docker compose version &> /dev/null; then
        log_info "Installing Docker Compose plugin..."
        run_command sudo apt-get install -y docker-compose-plugin
    fi
    
    # Install Certbot for SSL
    if ! command -v certbot &> /dev/null; then
        log_info "Installing Certbot..."
        run_command sudo apt-get install -y certbot python3-certbot-nginx
    fi
}

ensure_docker_running() {
    log_info "Checking Docker installation..."
    
    if ! command -v docker &> /dev/null; then
        log_info "Docker not found. Installing Docker..."
        echo 'Acquire::ForceIPv4 "true";' | sudo tee /etc/apt/apt.conf.d/99force-ipv4
        run_command sudo apt-get update
        run_command sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release software-properties-common
        run_command sudo mkdir -p /etc/apt/keyrings
        run_command curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
            | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        run_command sudo apt-get update
        run_command sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        run_command sudo usermod -aG docker root
        run_command sudo systemctl enable docker
        run_command sudo systemctl start docker
    elif ! sudo systemctl is-active --quiet docker; then
        run_command sudo systemctl start docker
    fi
    
    sudo docker info &> /dev/null || log_error "Docker is not running"
    log_info "Docker is installed and running"
}

deploy() {
    log_info "Starting deployment..."

    # Install required system packages first
    install_required_packages
    ensure_docker_running

    log_info "Preparing application directory: $APP_DIR"
    run_command mkdir -p $APP_DIR

    if [ -f /home/root/.env ]; then
        log_info "Copying .env file..."
        run_command cp /home/root/.env $APP_DIR
    else
        log_warn "No .env file found at /home/root/.env"
    fi

    if ! command -v nginx &> /dev/null; then
        log_info "Installing Nginx..."
        run_command sudo apt-get update
        run_command sudo apt-get install -y nginx
    fi

    log_info "Configuring Nginx..."
    sudo tee $NGINX_CONFIG > /dev/null <<EOL
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    root /home/root/app;
    index index.html index.htm;

    location / {
        try_files \$uri /index.html;
    }

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
}
EOL

    sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/
    sudo mkdir -p /var/www/html/.well-known/acme-challenge
    sudo chown -R www-data:www-data /var/www/html/.well-known

    sudo nginx -t || log_error "Nginx config test failed"
    sudo systemctl reload nginx

    log_info "Attempting SSL certificate..."
    sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL --quiet || log_warn "Certbot failed"

    log_info "Deploying Docker containers..."
    cd $APP_DIR
    sudo docker compose down || true
    sudo docker compose pull
    sudo docker compose up -d --force-recreate

    log_info "Deployment completed successfully!"
}

deploy
