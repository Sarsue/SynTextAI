#!/bin/bash
set -e

APP_DIR="/home/root/app"
ENV_FILE="$APP_DIR/.env"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 Starting SynTextAI deployment...${NC}"

# Error handler
error_exit() {
    echo -e "${RED}❌ $1${NC}" >&2
    exit 1
}

# Ensure required directories
mkdir -p "$APP_DIR/api/config" "$APP_DIR/searxng"

# Stop old containers
docker-compose down -v --remove-orphans || true
docker system prune -af --volumes || true

# Ensure nginx installed
if ! command -v nginx &>/dev/null; then
    apt-get update && apt-get install -y nginx || error_exit "Failed to install nginx"
fi

# Nginx config
cat > /etc/nginx/sites-available/syntextai << 'EOL'
server {
    listen 80;
    server_name syntextai.com www.syntextai.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }

    location /search/ {
        proxy_pass http://localhost:8080/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    client_max_body_size 100M;
}
EOL

# Enable site
ln -sf /etc/nginx/sites-available/syntextai /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx || error_exit "Failed to reload nginx"

# Pull & start containers
docker-compose pull || error_exit "Failed to pull images"
docker-compose up -d --build --force-recreate || error_exit "Failed to start containers"

echo -e "${GREEN}✅ Deployment complete! SynTextAI is live!${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
