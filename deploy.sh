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

# Create or update credentials file with proper escaping
# Using printf to properly escape newlines in private key
mkdir -p "$APP_DIR/api/config"
echo "Ensuring Firebase credentials are properly configured..."
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

# Set strict permissions
chmod 600 "$APP_DIR/api/config/credentials.json"

# Verify the credentials file was created
if [ ! -f "$APP_DIR/api/config/credentials.json" ]; then
    error_exit "Failed to create Firebase credentials file"
fi

echo -e "${GREEN}✓ Firebase credentials configured${NC}"

# Stop old containers
docker-compose down -v --remove-orphans || true
docker system prune -af --volumes || true

# Ensure nginx installed
if ! command -v nginx &>/dev/null; then
    apt-get update && apt-get install -y nginx || error_exit "Failed to install nginx"
fi

# Nginx config
cat > /etc/nginx/sites-available/syntextai << 'EOL'
# HTTP server - redirect to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name syntextai.com www.syntextai.com;
    
    # Let's Encrypt verification
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    # Redirect all other HTTP traffic to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name syntextai.com www.syntextai.com;

    # SSL configuration will be managed by certbot
    ssl_certificate /etc/letsencrypt/live/syntextai.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/syntextai.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Frame-Options "SAMEORIGIN";
    add_header Referrer-Policy "strict-origin";

    # Main application
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
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }

    # SearxNG search functionality
    location /search/ {
        proxy_pass http://localhost:8080/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }

    client_max_body_size 100M;
}
EOL

# Create certbot directory for challenges
mkdir -p /var/www/certbot

# Enable site
ln -sf /etc/nginx/sites-available/syntextai /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx || echo "Nginx reload failed, but continuing with deployment..."

# Install certbot if not installed
if ! command -v certbot &>/dev/null; then
    echo "Installing certbot..."
    apt-get update
    apt-get install -y certbot python3-certbot-nginx || echo "Failed to install certbot, continuing without SSL..."
fi

# Check if SSL certificate exists, if not, request one
if [ ! -f "/etc/letsencrypt/live/syntextai.com/fullchain.pem" ]; then
    echo "Requesting Let's Encrypt certificate..."
    certbot --nginx -d syntextai.com -d www.syntextai.com --non-interactive --agree-tos --email osas@osas-inc.com --redirect || \
        echo "Failed to obtain SSL certificate, continuing with HTTP only..."
    
    # Set up automatic renewal
    echo "Setting up automatic certificate renewal..."
    (crontab -l 2>/dev/null; echo "0 0,12 * * * /usr/bin/certbot renew --quiet") | crontab - || \
        echo "Failed to set up automatic renewal, certificates will need to be renewed manually"
else
    echo "SSL certificate already exists, skipping certificate request..."
    certbot renew --quiet || echo "Certificate renewal check failed, continuing..."
fi

# Pull & start containers
docker-compose pull || error_exit "Failed to pull images"
docker-compose up -d --build --force-recreate || error_exit "Failed to start containers"

echo -e "${GREEN}✅ Deployment complete! SynTextAI is live!${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
