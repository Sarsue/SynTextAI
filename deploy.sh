#!/bin/bash
set -e

# Configuration
APP_DIR="/home/root/app"
NGINX_CONFIG="/etc/nginx/sites-available/syntextai"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"

# Step 1: Install dependencies (idempotent)
echo "🚀 Installing dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io curl ufw nginx certbot python3-certbot-nginx

# Step 2: Install Docker Compose
echo "📦 Installing Docker Compose..."
DOCKER_COMPOSE_VERSION="v2.28.1"
sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Step 3: Start Docker
echo "🐳 Starting Docker..."
sudo systemctl enable --now docker
if ! sudo systemctl is-active --quiet docker; then
    echo "❌ Docker failed to start. Exiting."
    exit 1
fi

# Step 4: Ensure app directory exists
echo "📂 Setting up app directory..."
sudo mkdir -p $APP_DIR

# Step 5: Copy .env and Firebase credentials
echo "🔑 Setting up configuration..."
sudo cp /home/root/.env $APP_DIR/
sudo mkdir -p $APP_DIR/api/config
sudo cp /home/root/api/config/credentials.json $APP_DIR/api/config/

# Step 6: Configure firewall
echo "🔥 Configuring firewall..."
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw --force enable

# Step 7: Create temporary Nginx config for certbot
echo "🌐 Setting up temporary Nginx config..."
sudo tee $NGINX_CONFIG > /dev/null <<EOL
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
}
EOL

# Enable the site
sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# Step 8: Obtain SSL certificate if needed
echo "🔐 Obtaining SSL certificate..."
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    sudo certbot certonly --webroot -w /var/www/html -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL
fi

# Step 9: Update Nginx config with SSL
echo "🔄 Updating Nginx config with SSL..."
sudo tee $NGINX_CONFIG > /dev/null <<EOL
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $DOMAIN www.$DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    client_max_body_size 2G;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        send_timeout 300s;
        try_files \$uri \$uri/ /index.html;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /__/auth {
        proxy_pass https://syntextai-7e4c6.firebaseapp.com;
        proxy_ssl_server_name on;
        proxy_set_header Host syntextai-7e4c6.firebaseapp.com;
    }
}
EOL

# Test and reload Nginx
sudo nginx -t && sudo systemctl reload nginx

# Step 10: Start Docker containers
echo "🚀 Starting Docker containers..."
cd $APP_DIR
sudo docker-compose pull
sudo docker-compose up -d --remove-orphans --build

echo "✅ Deployment complete! Your site should now be available at https://$DOMAIN"
