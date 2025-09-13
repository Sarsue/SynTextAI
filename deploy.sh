#!/bin/bash
set -e

# Variables
APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"

# Function to ensure Docker is installed and running
ensure_docker_running() {
    if ! command -v docker &> /dev/null; then
        echo "Docker not found. Installing Docker..."
        # Update the apt package index and install required packages
        sudo apt-get update
        sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common
        
        # Add Docker's official GPG key
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
        
        # Add Docker repository
        sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
        
        # Update package index again and install Docker CE
        sudo apt-get update
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io
        
        # Add current user to docker group to avoid sudo
        sudo usermod -aG docker $USER
        
        # Enable and start Docker service
        sudo systemctl enable docker
        sudo systemctl start docker
    elif ! sudo systemctl is-active --quiet docker; then
        echo "Starting Docker service..."
        sudo systemctl start docker
    fi
    
    # Verify Docker is running
    if ! sudo docker info &> /dev/null; then
        echo "Failed to start Docker. Please check Docker installation."
        exit 1
    fi
    
    echo "Docker is installed and running."
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