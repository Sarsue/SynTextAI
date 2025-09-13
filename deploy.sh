#!/bin/bash
set -e

# Variables
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"

# Function to ensure Docker is running
ensure_docker_running() {
    if ! command -v docker &> /dev/null; then
        echo "Error: Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! sudo systemctl is-active --quiet docker; then
        echo "Starting Docker service..."
        sudo systemctl start docker
    fi
}

# Main deployment function
deploy() {
    # Ensure Docker is running
    ensure_docker_running

    # Set up application directory
    echo "Setting up application directory..."
    mkdir -p $APP_DIR

    # Copy environment files if they exist
    if [ -f /home/root/.env ]; then
        echo "Copying environment file..."
        cp /home/root/.env $APP_DIR
    fi

    # Configure Nginx
    echo "Configuring Nginx..."
    sudo tee $NGINX_CONFIG > /dev/null <<EOL
# [Previous Nginx config remains exactly the same]
EOL

    # Enable Nginx configuration
    sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/
    sudo mkdir -p /var/www/html/.well-known/acme-challenge
    sudo chown -R www-data:www-data /var/www/html/.well-known

    # Test and reload Nginx
    echo "Reloading Nginx..."
    if ! sudo nginx -t; then
        echo "Nginx configuration test failed. Exiting."
        exit 1
    fi
    sudo systemctl reload nginx

    # Try to obtain SSL certificate (non-blocking)
    echo "Attempting to obtain SSL certificate..."
    sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL --quiet || true

    # Deploy containers
    echo "Deploying containers..."
    cd $APP_DIR
    sudo docker-compose down || true
    sudo docker-compose pull
    sudo docker-compose up -d --force-recreate

    echo "Deployment completed successfully!"
}

deploy