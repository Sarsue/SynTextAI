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

# Create or update credentials file with proper escaping
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
docker-compose down -v --remove-orphans || true
docker system prune -af --volumes || true

# Install Nginx if not installed
if ! command -v nginx &>/dev/null; then
    echo -e "${YELLOW}Installing Nginx...${NC}"
    apt-get update && apt-get install -y nginx || error_exit "Failed to install nginx"
fi

# Create basic Nginx config without SSL first
echo -e "${GREEN}✓ Configuring Nginx...${NC}"
cat > /etc/nginx/sites-available/syntextai << 'EOL'
# HTTP server
server {
    listen 80;
    listen [::]:80;
    server_name _;
    
    # Let's Encrypt verification
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    # Redirect all HTTP to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server - will be enabled after SSL setup
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name _;
    
    # Default SSL cert (self-signed) - will be replaced by Let's Encrypt
    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Frame-Options "SAMEORIGIN";
    add_header Referrer-Policy "strict-origin";
    
    # Default response until properly configured
    location / {
        return 200 'SynTextAI is being configured. Please wait a moment and refresh.';
        add_header Content-Type text/plain;
    }
}
EOL

# Enable site
mkdir -p /var/www/certbot
ln -sf /etc/nginx/sites-available/syntextai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx config
nginx -t && systemctl reload nginx || echo -e "${YELLOW}⚠️ Nginx reload failed, but continuing...${NC}"

# Install certbot if not installed
if ! command -v certbot &>/dev/null; then
    echo -e "${YELLOW}Installing certbot...${NC}"
    apt-get update
    apt-get install -y certbot python3-certbot-nginx || echo -e "${YELLOW}⚠️ Failed to install certbot, continuing without SSL...${NC}"
fi

# Function to setup SSL
setup_ssl() {
    echo -e "${GREEN}✓ Setting up SSL certificate...${NC}"
    
    # Stop Nginx temporarily
    systemctl stop nginx || true
    
    # Get certificate using standalone mode (Nginx stopped)
    if certbot certonly --standalone -d $DOMAIN -d www.$DOMAIN \
        --non-interactive --agree-tos --email $EMAIL \
        --preferred-challenges http-01 || return 1
    
    # Update Nginx config with actual domain
    sed -i "s/server_name _/server_name $DOMAIN www.$DOMAIN/" /etc/nginx/sites-available/syntextai
    
    # Update SSL certificate paths
    sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;|" /etc/nginx/sites-available/syntextai
    sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;|" /etc/nginx/sites-available/syntextai
    
    # Add SSL configuration
    if ! grep -q "ssl_dhparam" /etc/nginx/sites-available/syntextai; then
        sed -i '/ssl_certificate_key/a \n    # SSL configuration\n    include /etc/letsencrypt/options-ssl-nginx.conf;\n    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n' /etc/nginx/sites-available/syntextai
    fi
    
    # Add proxy configuration
    if ! grep -q "location / {" /etc/nginx/sites-available/syntextai; then
        sed -i '/ssl_dhparam/a \n    # Main application\n    location / {\n        proxy_pass http://localhost:3000;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection "upgrade";\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_cache_bypass $http_upgrade;\n        proxy_read_timeout 300;\n        proxy_connect_timeout 300;\n        proxy_send_timeout 300;\n    }\n' /etc/nginx/sites-available/syntextai
    fi
    
    # Add SearxNG configuration if not exists
    if ! grep -q "location /search/" /etc/nginx/sites-available/syntextai; then
        cat >> /etc/nginx/sites-available/syntextai << 'EOL'

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
EOL
    fi
    
    # Restart Nginx
    systemctl restart nginx
    
    # Set up auto-renewal
    echo -e "${GREEN}✓ Setting up SSL auto-renewal...${NC}"
    (crontab -l 2>/dev/null; echo "0 0,12 * * * /usr/bin/certbot renew --quiet --deploy-hook 'systemctl reload nginx'") | crontab - || \
        echo -e "${YELLOW}⚠️ Failed to set up auto-renewal, certificates will need to be renewed manually${NC}"
    
    return 0
}

# Try to setup SSL, but continue if it fails
setup_ssl || echo -e "${YELLOW}⚠️ SSL setup failed, continuing without HTTPS...${NC}"

# Pull and start containers
echo -e "${GREEN}✓ Starting Docker containers...${NC}"
if ! docker-compose pull; then
    echo -e "${YELLOW}⚠️ Failed to pull some images, trying to continue with local images...${NC}
fi

if ! docker-compose up -d --build --force-recreate; then
    echo -e "${YELLOW}⚠️ Failed to start containers, trying to continue...${NC}
    docker-compose up -d || error_exit "Failed to start containers after retry"
fi

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

# Final status
echo -e "\n${GREEN}✅ Deployment complete! SynTextAI is live!${NC}\n"
echo -e "${YELLOW}📋 Container status:${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Show access URLs
IP=$(curl -s ifconfig.me)
echo -e "\n${GREEN}🌐 Access URLs:${NC}"
echo -e "- http://$IP"
echo -e "- https://$DOMAIN"
echo -e "- https://www.$DOMAIN"

echo -e "\n${GREEN}✨ Deployment successful!${NC}"
