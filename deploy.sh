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
    local packages=("docker.io" "docker-compose-plugin" "jq" "curl")
    local missing_packages=()
    
    for pkg in "${packages[@]}"; do
        if ! dpkg -l | grep -q "^ii.*$pkg"; then
            missing_packages+=("$pkg")
        fi
    done
    
    if [ ${#missing_packages[@]} -ne 0 ]; then
        print_status "Installing missing packages: ${missing_packages[*]}"
        apt-get update
        apt-get install -y "${missing_packages[@]}" || error_exit "Failed to install required packages"
    fi
}

# Setup Docker environment
setup_docker() {
    print_status "Setting up Docker environment..."
    systemctl enable --now docker || error_exit "Failed to enable Docker service"
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

# Deploy application using Docker Compose
deploy_application() {
    cd "$APP_DIR" || error_exit "Failed to change to app directory"
    
    # Stop and clean up existing containers
    print_status "Stopping and cleaning up existing containers..."
    docker-compose down --remove-orphans || print_warning "No running containers to stop"
    
    # Pull latest images
    print_status "Pulling latest Docker images..."
    docker-compose pull || error_exit "Failed to pull Docker images"
    
    # Start services
    print_status "Starting services..."
    docker-compose up -d --build --remove-orphans || error_exit "Failed to start services"
    
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
    docker-compose exec -T app alembic upgrade head || 
        print_warning "Failed to run migrations (container might still be starting)"
}

# Verify deployment
verify_deployment() {
    print_status "Verifying deployment..."
    
    # Check if containers are running
    if ! docker-compose ps | grep -q "Up"; then
        error_exit "Some containers are not running"
    fi
    
    # Check application health
    local health_check_url="http://localhost/health"
    local max_retries=30
    local retry_count=0
    
    print_status "Waiting for application to be ready..."
    until curl -s -f "$health_check_url" >/dev/null; do
        retry_count=$((retry_count + 1))
        if [ $retry_count -ge $max_retries ]; then
            error_exit "Application failed to start. Check container logs with: docker-compose logs"
        fi
        sleep 5
        echo -n "."
    done
    
    echo -e "\n${GREEN}Application is up and running!${NC}"
}

# Main function
main() {
    print_status "Starting deployment of $APP_NAME..."
    
    # Install required packages
    install_required_packages
    
    # Setup Docker
    setup_docker
    
    # Setup app directory
    setup_app_directory
    
    # Deploy application
    deploy_application
    
    # Verify deployment
    verify_deployment
    
    print_status "Deployment completed successfully!"
    print_status "Application URL: https://syntextai.com"
    print_status "Run 'docker-compose logs -f' to view logs"
}

# Execute main function
main "$@"

# Error handler
error_exit() {
    echo -e "${RED}❌ Error: $1${NC}" >&2
    exit 1
}

# Install required system packages
install_dependencies() {
    echo -e "${GREEN}📦 Installing required packages...${NC}"
    apt-get update
    apt-get install -y \
        nginx \
        certbot \
        python3-certbot-nginx \
        docker.io \
        docker-compose
    
    # Ensure Docker is running
    systemctl enable --now docker
}

# Configure Nginx
setup_nginx() {
    echo -e "${GREEN}🔧 Configuring Nginx...${NC}"
    
    # Create Nginx config
    cat > /etc/nginx/sites-available/${DOMAIN} << EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};
    
    # Redirect HTTP to HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN} www.${DOMAIN};
    
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    
    # API requests
    location /api/ {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_cache_bypass \$http_upgrade;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    # Frontend
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_cache_bypass \$http_upgrade;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

    # Enable the site
    ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    
    # Test and reload Nginx
    nginx -t && systemctl reload nginx
}

# Setup SSL with Certbot
setup_ssl() {
    echo -e "${GREEN}🔒 Setting up SSL with Certbot...${NC}"
    
    # Stop Nginx temporarily
    systemctl stop nginx
    
    # Obtain SSL certificate
    certbot certonly --standalone \
        -d ${DOMAIN} \
        -d www.${DOMAIN} \
        --non-interactive \
        --agree-tos \
        --email ${EMAIL} \
        --preferred-challenges http
    
    # Start Nginx
    systemctl start nginx
    
    # Set up automatic renewal
    (crontab -l 2>/dev/null; echo "0 0,12 * * * certbot renew --quiet") | crontab - || \
        echo "Failed to set up automatic renewal"
}

# Deploy application with Docker Compose
deploy_application() {
    echo -e "${GREEN}🚀 Deploying application...${NC}"
    cd "${APP_DIR}"
    
    # Create .env file if it doesn't exist
    if [ ! -f ".env" ]; then
        cat > .env << EOF
# Application Settings
ENVIRONMENT=production
LOG_LEVEL=INFO

# Database Settings
DB_HOST=db
DB_PORT=5432
DB_NAME=${DB_NAME:-syntextai}
DB_USER=${DB_USER:-postgres}
DB_PASSWORD=${DB_PASSWORD:-changeme}

# JWT Settings
SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32)}
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# CORS Settings
ALLOWED_ORIGINS=*
EOF
    fi
    
    # Set proper permissions for .env
    chmod 600 .env
    
    # Stop and remove existing containers
    echo -e "${GREEN}🛑 Stopping existing containers...${NC}"
    docker-compose down --remove-orphans || true

    # Pull latest images
    echo -e "${GREEN}⬇️  Pulling latest images...${NC}"
    docker-compose pull

    # Build and start services
    echo -e "${GREEN}🚀 Starting services...${NC}"
    docker-compose up -d --build
    
    # Show container status
    echo -e "\n${GREEN}📊 Container status:${NC}"
    docker-compose ps
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
    
    # Setup Nginx
    setup_nginx
    
    # Setup SSL
    setup_ssl
    
    # Deploy the application
    deploy_application

    # Show completion message
    echo -e "\n${GREEN}✅ Deployment complete!${NC}"
    echo -e "\n${GREEN}🌐 Access your application at:${NC}"
    echo -e "- https://${DOMAIN}"
    echo -e "- https://search.${DOMAIN}"
    
    # Show running containers
    echo -e "\n${GREEN}🐳 Running containers:${NC}"
    docker ps
}

# Run the main function
main "$@"
