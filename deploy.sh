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

# Step 2: Install Docker Compose plugin
echo "Installing Docker Compose plugin..."
sudo apt-get update
sudo apt-get install -y docker-compose-plugin

# Verify installation
echo "Verifying Docker Compose installation..."
docker compose version || (echo "Docker Compose installation failed" && exit 1)

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

# Step 4: Create self-signed certificate first
echo "Creating self-signed certificate..."
sudo mkdir -p /etc/ssl/certs /etc/ssl/private
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/nginx-selfsigned.key \
    -out /etc/ssl/certs/nginx-selfsigned.crt \
    -subj "/CN=$DOMAIN" \
    -addext "subjectAltName=DNS:$DOMAIN,DNS:www.$DOMAIN"

# Step 5: Set up initial Nginx configuration with self-signed cert
echo "Setting up initial Nginx configuration..."
sudo tee $NGINX_CONFIG > /dev/null <<EOL
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
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN www.$DOMAIN;

    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;
    
    # SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers 'EECDH+AESGCM:EDH+AESGCM:AES256+EECDH:AES256+EDH';
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # Add debug headers
    add_header X-Debug-Server-Name $host;
    add_header X-Debug-Request-URI $request_uri;
    add_header X-Debug-Remote-Addr $remote_addr;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Debug logging
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

# Enable the Nginx configuration
echo "Enabling Nginx configuration..."
sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Create necessary directories with proper permissions
echo "Setting up Nginx directories..."
sudo mkdir -p /var/www/html/.well-known/acme-challenge
sudo mkdir -p /var/log/nginx/
sudo touch /var/log/nginx/error.log
sudo touch /var/log/nginx/access.log
sudo touch /var/log/nginx/app_error.log
sudo touch /var/log/nginx/app_access.log

# Set proper ownership and permissions
sudo chown -R www-data:www-data /var/www/html/.well-known
sudo chown -R www-data:www-data /var/log/nginx/
sudo chmod -R 755 /var/log/nginx/

# Test Nginx configuration first
echo "Testing Nginx configuration..."
if ! sudo nginx -t; then
    echo "Nginx configuration test failed. Showing error log:"
    sudo tail -n 50 /var/log/nginx/error.log || echo "No error log found"
    exit 1
fi

# Reload Nginx with self-signed certificate
echo "Reloading Nginx with self-signed certificate..."
sudo systemctl restart nginx

# Check if Nginx started successfully
if ! systemctl is-active --quiet nginx; then
    echo "Nginx failed to start. Showing status and logs:"
    sudo systemctl status nginx
    sudo journalctl -u nginx -n 50 --no-pager
    exit 1
fi

# Step 6: Obtain Let's Encrypt certificate
echo "Obtaining SSL certificate from Let's Encrypt..."
if sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL; then
    echo "Successfully obtained Let's Encrypt certificate!"
    # Update Nginx to use the new certificate
    sudo sed -i 's|/etc/ssl/certs/nginx-selfsigned.crt|/etc/letsencrypt/live/$DOMAIN/fullchain.pem|' $NGINX_CONFIG
    sudo sed -i 's|/etc/ssl/private/nginx-selfsigned.key|/etc/letsencrypt/live/$DOMAIN/privkey.pem|' $NGINX_CONFIG
    sudo systemctl restart nginx
else
    echo "Failed to obtain Let's Encrypt certificate, continuing with self-signed certificate"
fi

# Step 10: Bring up the Docker containers using Docker Compose
echo "Bringing up Docker containers with Docker Compose..."
sudo docker-compose -f docker-compose.yml up -d

echo "Deployment completed successfully!"