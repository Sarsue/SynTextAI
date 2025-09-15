#!/bin/bash
set -e

# Configuration
APP_DIR="/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'  # No Color

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
