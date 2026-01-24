#!/bin/bash
set -e

# Variables
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"  # Replace with your Firebase project ID

echo "=== 🚀 Starting deployment for $DOMAIN ==="

wait_for_apt_locks() {
    local timeout_seconds=${1:-300}
    local start_ts
    start_ts=$(date +%s)

    while true; do
        local now_ts elapsed
        now_ts=$(date +%s)
        elapsed=$((now_ts - start_ts))
        if [ "$elapsed" -ge "$timeout_seconds" ]; then
            echo "❌ Timed out waiting for apt/dpkg locks after ${timeout_seconds}s"
            sudo ps aux | grep -E "(apt-get|apt\.|unattended-upgrades|dpkg)" | grep -v grep || true
            return 1
        fi

        if command -v lsof >/dev/null 2>&1; then
            if sudo lsof /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; then
                echo "⏳ Waiting for apt/dpkg lock... (${elapsed}s elapsed)"
                sleep 5
                continue
            fi
        else
            if sudo ps aux | grep -E "(apt-get|apt\.|unattended-upgrades|dpkg)" | grep -v grep >/dev/null 2>&1; then
                echo "⏳ Waiting for apt/dpkg processes to finish... (${elapsed}s elapsed)"
                sleep 5
                continue
            fi
        fi

        return 0
    done
}

apt_get_with_lock_retry() {
    local timeout_seconds=${1:-600}
    shift

    local start_ts
    start_ts=$(date +%s)

    while true; do
        set +e
        local output
        output=$(sudo apt-get "$@" 2>&1)
        local exit_code=$?
        set -e

        if [ "$exit_code" -eq 0 ]; then
            echo "$output"
            return 0
        fi

        if echo "$output" | grep -q "Could not get lock"; then
            local now_ts elapsed
            now_ts=$(date +%s)
            elapsed=$((now_ts - start_ts))
            if [ "$elapsed" -ge "$timeout_seconds" ]; then
                echo "$output" >&2
                echo "❌ Timed out waiting for apt locks after ${timeout_seconds}s" >&2
                sudo ps aux | grep -E "(apt-get|apt\.|unattended-upgrades|dpkg)" | grep -v grep || true
                return "$exit_code"
            fi

            echo "⏳ apt-get is locked. Waiting 10s then retrying... (${elapsed}s elapsed)" >&2
            sleep 10
            continue
        fi

        echo "$output" >&2
        return "$exit_code"
    done
}

# Step 1: Verify system dependencies are installed (do not apt-get during deploy)
echo "[1/9] Verifying system dependencies..."
if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Docker is not installed on the host. Install Docker once on the droplet, then rerun deploy." 
    exit 1
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    echo "❌ Docker Compose not found on the host. Install Docker Compose once on the droplet, then rerun deploy." 
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "❌ curl not found on the host. Install curl once on the droplet, then rerun deploy." 
    exit 1
fi
if ! command -v nginx >/dev/null 2>&1; then
    echo "❌ nginx not found on the host. Install nginx once on the droplet, then rerun deploy." 
    exit 1
fi
if ! command -v certbot >/dev/null 2>&1; then
    echo "❌ certbot not found on the host. Install certbot once on the droplet, then rerun deploy." 
    exit 1
fi

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

# Ensure docker-compose.yml is available inside APP_DIR so we can run compose from there.
# This keeps relative bind mounts (e.g. ./api/config) resolving to /home/root/app/api/config.
if [ -f /home/root/docker-compose.yml ]; then
    cp /home/root/docker-compose.yml "$APP_DIR/docker-compose.yml"
fi

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

    client_max_body_size 100M;

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
(cd "$APP_DIR" && sudo $COMPOSE_CMD -f docker-compose.yml up -d)

echo "✅ Deployment completed successfully!"
echo "📦 Docker images pulled and services started"
