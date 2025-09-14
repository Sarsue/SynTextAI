#!/bin/bash
set -e

APP_DIR="/home/root/app"
DOMAIN="syntextai.com"
EMAIL="osas@osas-inc.com"
NGINX_CONFIG="/etc/nginx/sites-available/syntextaiapp"
FIREBASE_PROJECT="docsynth-fbb02"

echo "🔧 Updating system and installing dependencies..."
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx curl

echo "📂 Setting up application directory..."
mkdir -p $APP_DIR

if [ -f /home/root/.env ]; then
    echo "✅ Copying environment file..."
    cp /home/root/.env $APP_DIR
else
    echo "❌ .env file missing at /home/root/.env"
    exit 1
fi

echo "🌐 Configuring Nginx..."
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

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_buffering off;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
    }

    location /__/auth {
        proxy_pass https://$FIREBASE_PROJECT.firebaseapp.com;
        proxy_ssl_server_name on;
        proxy_set_header Host $FIREBASE_PROJECT.firebaseapp.com;
    }
}
EOL

sudo ln -sf $NGINX_CONFIG /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

echo "🔒 Obtaining/renewing SSL certificate..."
sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m $EMAIL || true

echo "🐳 Cleaning up old Docker resources..."
sudo docker compose down || true
sudo docker system prune -af --volumes

echo "⬇️ Pulling latest Docker images..."
sudo docker compose pull

echo "🚀 Starting containers..."
sudo docker compose up -d --force-recreate

echo "✅ Deployment completed successfully!"
