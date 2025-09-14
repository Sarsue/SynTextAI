#!/bin/bash
set -e

# Load .env file if it exists
if [ -f ".env" ]; then
    set -a
    . .env
    set +a
fi

# Configuration
APP_DIR="/home/root/app"
ENV_FILE="$APP_DIR/.env"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"

# Validate required environment variables
required_vars=(
  "FIREBASE_PROJECT_ID"
  "FIREBASE_PRIVATE_KEY"
  "FIREBASE_CLIENT_EMAIL"
  "FIREBASE_PRIVATE_KEY_ID"
  "FIREBASE_CLIENT_ID"
  "FIREBASE_CLIENT_CERT_URL"
)

for var in "${required_vars[@]}"; do
  if [ -z "${!var}" ]; then
    echo -e "${RED}❌ Missing required env var: $var${NC}"
    exit 1
  fi
done

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 Starting SynTextAI deployment...${NC}"

error_exit() {
    echo -e "${RED}❌ $1${NC}" >&2
    exit 1
}

# Ensure required dirs
mkdir -p "$APP_DIR/api/config" "$APP_DIR/searxng"

# Check Firebase vars
for var in FIREBASE_PROJECT_ID FIREBASE_PRIVATE_KEY FIREBASE_CLIENT_EMAIL; do
  [ -z "${!var}" ] && error_exit "Missing required env var: $var"
done

# Write Firebase credentials
echo -e "${GREEN}✓ Writing Firebase credentials...${NC}"
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
chown root:root "$APP_DIR/api/config/credentials.json"

# Clean old containers (safe)
echo -e "${GREEN}✓ Stopping old containers...${NC}"
docker compose down -v --remove-orphans || true

# Ensure nginx installed
if ! command -v nginx &>/dev/null; then
  echo -e "${YELLOW}Installing nginx...${NC}"
  apt-get update && apt-get install -y nginx || error_exit "Failed to install nginx"
fi

# Ensure baseline nginx config
if [ ! -f /etc/nginx/sites-available/syntextai ]; then
cat > /etc/nginx/sites-available/syntextai << 'EOL'
server {
    listen 80;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}
EOL
ln -sf /etc/nginx/sites-available/syntextai /etc/nginx/sites-enabled/
mkdir -p /var/www/certbot
fi

systemctl reload nginx || true

# Ensure certbot
if ! command -v certbot &>/dev/null; then
  apt-get install -y certbot python3-certbot-nginx || echo -e "${YELLOW}⚠️ Certbot not installed${NC}"
fi

# SSL only if not present
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  echo -e "${GREEN}✓ Obtaining SSL cert for $DOMAIN...${NC}"
  systemctl stop nginx || true
  certbot certonly --standalone -d "$DOMAIN" -d "www.$DOMAIN" \
    --non-interactive --agree-tos --email "$EMAIL" \
    --preferred-challenges http-01 || echo -e "${YELLOW}⚠️ Certbot failed, continuing without SSL${NC}"
  systemctl start nginx || true
fi

# Start containers
echo -e "${GREEN}✓ Starting Docker containers...${NC}"
docker compose pull || echo -e "${YELLOW}⚠️ Pull failed, using local images${NC}"
docker compose up -d --build --force-recreate || error_exit "Docker compose up failed"

# Setup auto-renew once
if ! crontab -l 2>/dev/null | grep -q certbot; then
  (crontab -l 2>/dev/null; echo "0 0,12 * * * certbot renew --quiet --deploy-hook 'systemctl reload nginx'") | crontab -
fi

echo -e "${GREEN}✅ Deployment complete!${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
