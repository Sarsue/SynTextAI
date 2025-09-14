#!/bin/bash
set -e

# Configuration
APP_DIR="/home/root/app"
ENV_FILE="$APP_DIR/.env"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"

# Colors
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

# Firebase credentials
echo -e "${GREEN}✓ Setting up Firebase credentials...${NC}"
cat > "$APP_DIR/api/config/credentials.json" << EOF
{
  "type": "service_account",
  "project_id": "${FIREBASE_PROJECT_ID}",
  "private_key_id": "${FIREBASE_PRIVATE_KEY_ID}",
  "private_key": "$(printf "%s" "$FIREBASE_PRIVATE_KEY" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')",
  "client_email": "${FIREBASE_CLIENT_EMAIL}",
  "client_id": "${FIREBASE_CLIENT_ID}",
  "auth_uri": "${FIREBASE_AUTH_URI:-https://accounts.google.com/o/oauth2/auth}",
  "token_uri": "${FIREBASE_TOKEN_URI:-https://oauth2.googleapis.com/token}",
  "auth_provider_x509_cert_url": "${FIREBASE_AUTH_PROVIDER_CERT_URL:-https://www.googleapis.com/oauth2/v1/certs}",
  "client_x509_cert_url": "${FIREBASE_CLIENT_CERT_URL}",
  "universe_domain": "googleapis.com"
}
EOF
chmod 600 "$APP_DIR/api/config/credentials.json"

# Stop old containers
echo -e "${GREEN}✓ Cleaning up old containers...${NC}"
docker compose down -v --remove-orphans || true
docker system prune -af --volumes || true

# Install Nginx if missing
if ! command -v nginx &>/dev/null; then
    echo -e "${YELLOW}Installing Nginx...${NC}"
    apt-get update && apt-get install -y nginx || error_exit "Failed to install nginx"
fi

# Nginx baseline config (HTTP redirect + dummy SSL)
echo -e "${GREEN}✓ Configuring Nginx...${NC}"
cat > /etc/nginx/sites-available/syntextai << 'EOL'
server {
    listen 80;
    listen [::]:80;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2;
    server_name _;
    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Frame-Options "SAMEORIGIN";
    add_header Referrer-Policy "strict-origin";

    location / {
        return 200 'SynTextAI is being configured. Please wait.';
        add_header Content-Type text/plain;
    }
}
EOL

mkdir -p /var/www/certbot
ln -sf /etc/nginx/sites-available/syntextai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx || echo -e "${YELLOW}⚠️ Nginx reload failed (continuing)...${NC}"

# Install certbot if missing
if ! command -v certbot &>/dev/null; then
    echo -e "${YELLOW}Installing certbot...${NC}"
    apt-get update
    apt-get install -y certbot python3-certbot-nginx || echo -e "${YELLOW}⚠️ Certbot install failed, continuing without SSL...${NC}"
fi

# SSL setup
setup_ssl() {
    echo -e "${GREEN}✓ Setting up
