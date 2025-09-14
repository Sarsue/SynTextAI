#!/bin/bash
set -e

# Variables
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"

# Step 1: Install dependencies
echo "Updating system and installing dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io nginx certbot python3-certbot-nginx curl ufw

# Step 2: Install Docker Compose
echo "Installing Docker Compose..."
DOCKER_COMPOSE_VERSION="v2.28.1"
sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Step 3: Start Docker
echo "Starting Docker..."
sudo systemctl start docker
sudo systemctl enable docker
if ! sudo systemctl is-active --quiet docker; then
    echo "Docker failed to start. Exiting."
    exit 1
fi

# Step 4: Ensure app directory exists
mkdir -p $APP_DIR

# Step 5: Copy .env
if [ -f /home/root/.env ]; then
    echo "Copying .env file..."
    cp /home/root/.env $APP_DIR
else
    echo "Error: .env file missing at /home/root/.env"
    exit 1
fi

# Step 6: Ensure Firebase config is present
if [ ! -f "$APP_DIR/api/config/credentials.json" ]; then
    echo "Copying Firebase credentials..."
    mkdir -p $APP_DIR/api/config
    cp /home/root/api/config/credentials.json $APP_DIR/api/config/
fi

# Step 7: Configure Nginx
echo "Setting up Nginx config..."
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
        proxy_pass https://$FIREBASE_PROJECT.firebaseapp.com;
        proxy_ssl_server_name on;
        proxy_set_header Host $FIREBASE_PROJECT.firebaseapp.com;
    }
}
EOL

sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/

# Step 8: Allow HTTP/HTTPS through firewall
sudo ufw allow 80
sudo ufw allow 443

# Step 9: Create Certbot validation directory
sudo mkdir -p /var/www/html/.well-known/acme-challenge
sudo chown -R www-data:www-data /var/www/html/.well-known

# Step 10: Reload Nginx
sudo nginx -t
sudo systemctl reload nginx

# Step 11: Obtain or renew SSL certificate
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "Obtaining SSL certificate..."
    sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL
else
    echo "Renewing SSL certificate if needed..."
    sudo certbot renew --quiet
fi

# Step 12: Start Docker containers
echo "Starting Docker containers..."
sudo docker-compose -f $APP_DIR/docker-compose.yml pull
sudo docker-compose -f $APP_DIR/docker-compose.yml up -d --build

echo "✅ Deployment complete with SSL and Firebase config!"
