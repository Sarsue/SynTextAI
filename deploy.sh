#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Variables
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"  # Replace with your Firebase project ID

# Step 1: Update system and install necessary dependencies
echo "Updating system and installing dependencies..."
sudo apt-get update
sudo apt-get install -y docker.io nginx certbot python3-certbot-nginx curl

# Step 2: Install Docker Compose
echo "Installing Docker Compose..."
DOCKER_COMPOSE_VERSION="v2.28.1"
sudo curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Step 3: Start and enable Docker
echo "Starting Docker..."
sudo systemctl start docker
sudo systemctl enable docker

# Check Docker status
if ! sudo systemctl is-active --quiet docker; then
    echo "Docker service failed to start. Exiting."
    exit 1
fi

# Step 4: Set up application directory if it doesn't exist
echo "Setting up application directory..."
mkdir -p $APP_DIR

# Step 5: Copy environment file if it exists
if [ -f /home/root/.env ]; then
    echo "Copying environment file..."
    cp /home/root/.env $APP_DIR
else
    echo "Error: .env file not found at /home/root/.env"
    exit 1
fi

# Step 6: Configure Nginx for FastAPI and SSL
echo "Setting up Nginx configuration for FastAPI and SSL..."
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

        # Increase timeout settings
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        send_timeout 300s;

        try_files $uri $uri/ /index.html;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        # Increase timeout settings
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

# Enable the Nginx configuration
echo "Enabling Nginx configuration..."
sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/

# Step 7: Create the validation directory for Certbot
echo "Creating the validation directory for Certbot..."
sudo mkdir -p /var/www/html/.well-known/acme-challenge
sudo chown -R www-data:www-data /var/www/html/.well-known

# Step 8: Reload Nginx to apply the configuration
echo "Reloading Nginx..."
if ! sudo nginx -t; then
    echo "Nginx configuration test failed. Exiting."
    exit 1
fi
sudo systemctl reload nginx

# Step 9: Obtain SSL certificate with Certbot
echo "Obtaining SSL certificate..."
if ! sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL; then
    echo "Failed to obtain SSL certificate. Exiting."
    exit 1
fi

# Step 10: Bring up the Docker containers using Docker Compose
echo "Bringing up Docker containers with Docker Compose..."
sudo docker-compose -f docker-compose.yml up -d

echo "Deployment completed successfully!"
