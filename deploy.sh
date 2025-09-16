#!/bin/bash
set -e

# Variables
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"  # Replace with your Firebase project ID

echo "=== 🚀 Starting deployment for $DOMAIN ==="

# Step 1: Update system and install necessary dependencies
echo "[1/9] Updating system and installing dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io nginx certbot python3-certbot-nginx curl docker-compose-plugin

# Step 2: Detect Docker Compose command
echo "[2/9] Detecting Docker Compose..."
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif docker-compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    echo "❌ Docker Compose not found"
    exit 1
fi
echo "✅ Using Docker Compose command: $COMPOSE_CMD"

# Step 3: Start and enable Docker
echo "[3/9] Starting Docker..."
sudo systemctl enable --now docker
if ! sudo systemctl is-active --quiet docker; then
    echo "❌ Docker service failed to start."
    exit 1
fi

# Step 4: Set up application directory
echo "[4/9] Setting up application directory..."
mkdir -p "$APP_DIR"

# Step 5: Copy environment file
if [ -f /home/root/.env ]; then
    echo "✅ Copying environment file..."
    cp /home/root/.env "$APP_DIR"
else
    echo "❌ .env file not found at /home/root/.env"
    exit 1
fi

# Step 6: Create self-signed certificate
echo "[5/9] Creating self-signed certificate..."
sudo mkdir -p /etc/ssl/certs /etc/ssl/private
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/nginx-selfsigned.key \
    -out /etc/ssl/certs/nginx-selfsigned.crt \
    -subj "/CN=$DOMAIN" \
    -addext "subjectAltName=DNS:$DOMAIN,DNS:www.$DOMAIN"

# Step 7: Configure Nginx
echo "[6/9] Setting up Nginx configuration..."

# Clean up any old configs
sudo rm -f /etc/nginx/sites-enabled/syntextaiapp
sudo rm -f /etc/nginx/sites-available/syntextaiapp

sudo tee "$NGINX_CONFIG" > /dev/null <<EOL
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
        default_type text/plain;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $DOMAIN www.$DOMAIN;

    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers 'EECDH+AESGCM:EDH+AESGCM:AES256+EECDH:AES256+EDH';
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # Debug headers
    add_header X-Debug-Server-Name '\$host';
    add_header X-Debug-Request-URI '\$request_uri';
    add_header X-Debug-Remote-Addr '\$remote_addr';

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

        access_log /var/log/nginx/app_access.log;
        error_log /var/log/nginx/app_error.log;
    }

    location /__/auth {
        proxy_pass https://$FIREBASE_PROJECT.firebaseapp.com;
        proxy_ssl_server_name on;
        proxy_set_header Host $FIREBASE_PROJECT.firebaseapp.com;
    }
}
EOL

# Enable Nginx config
sudo ln -sf "$NGINX_CONFIG" /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Setup Nginx dirs
echo "Creating Nginx directories..."
sudo mkdir -p /var/www/html/.well-known/acme-challenge
sudo mkdir -p /var/log/nginx/
sudo touch /var/log/nginx/{error,access,app_error,app_access}.log
sudo chown -R www-data:www-data /var/www/html/.well-known /var/log/nginx/
sudo chmod -R 755 /var/log/nginx/

# Test and restart
echo "Testing Nginx config..."
sudo nginx -t
sudo systemctl restart nginx

# Step 8: Obtain Let's Encrypt cert
echo "[7/9] Obtaining SSL certificate..."
if sudo certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"; then
    echo "✅ Successfully obtained Let's Encrypt certificate!"
    sudo sed -i "s|/etc/ssl/certs/nginx-selfsigned.crt|/etc/letsencrypt/live/$DOMAIN/fullchain.pem|" "$NGINX_CONFIG"
    sudo sed -i "s|/etc/ssl/private/nginx-selfsigned.key|/etc/letsencrypt/live/$DOMAIN/privkey.pem|" "$NGINX_CONFIG"
    sudo systemctl reload nginx
else
    echo "⚠️ Failed to obtain Let's Encrypt certificate, continuing with self-signed."
fi

# Step 8.5: Pre-pull Docker images with retry logic
echo "[8/9] Pre-pulling Docker images..."
MAX_RETRIES=5
RETRY_DELAY=15

pull_image() {
    local image=$1
    for i in $(seq 1 $MAX_RETRIES); do
        echo "  Attempt $i/$MAX_RETRIES: Pulling $image..."
        if sudo docker pull $image; then
            echo "  ✅ Successfully pulled $image"
            return 0
        fi
        if [ $i -eq $MAX_RETRIES ]; then
            echo "  ❌ Failed to pull $image after $MAX_RETRIES attempts"
            return 1
        fi
        echo "  ⏳ Pull failed, retrying in $RETRY_DELAY seconds..."
        sleep $RETRY_DELAY
    done
}

# Pull all required images
pull_image "osasdeeon/syntextai:latest" || exit 1
pull_image "searxng/searxng:latest" || exit 1

# Step 9: Bring up Docker containers
echo "[9/9] Launching Docker containers..."
sudo $COMPOSE_CMD -f docker-compose.yml up -d

echo "✅ Deployment completed successfully!"
echo "📦 Docker images pulled and services started"
