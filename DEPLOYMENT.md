# SynTextAI Deployment Guide

## Prerequisites

1. A server with Ubuntu 20.04/22.04 LTS
2. Domain name pointing to your server's IP
3. Docker and Docker Compose installed
4. Ports 80, 443, and 3000 open in your firewall

## Deployment Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/syntextai.git /root/app
   cd /root/app
   ```

2. **Set up environment variables**
   Create a `.env` file in the root directory with the following variables:
   ```
   # Application Settings
   ENVIRONMENT=production
   LOG_LEVEL=INFO
   
   # Database Settings
   DB_NAME=syntextai
   DB_USER=postgres
   DB_PASSWORD=your_secure_password
   
   # JWT Settings
   SECRET_KEY=your_jwt_secret_key
   ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=1440
   
   # CORS Settings
   ALLOWED_ORIGINS=*
   
   # Firebase Configuration
   FIREBASE_PROJECT_ID=your-project-id
   FIREBASE_CLIENT_EMAIL=your-client-email@project.iam.gserviceaccount.com
   FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYour private key here\n-----END PRIVATE KEY-----\n"
   FIREBASE_PRIVATE_KEY_ID=your-private-key-id
   FIREBASE_CLIENT_ID=your-client-id
   FIREBASE_CLIENT_CERT_URL=your-client-cert-url
   ```

3. **Make the deploy script executable**
   ```bash
   chmod +x deploy.sh
   ```

4. **Run the deployment script**
   ```bash
   ./deploy.sh
   ```

## Post-Deployment

1. **Verify the deployment**
   ```bash
   docker ps
   docker-compose logs -f
   ```

2. **Set up monitoring** (optional)
   - Configure log rotation for Docker containers
   - Set up monitoring for disk space and resource usage

## Updating the Application

1. Pull the latest changes
   ```bash
   cd /root/app
   git pull
   ```

2. Run the deploy script again
   ```bash
   ./deploy.sh
   ```

## Troubleshooting

1. **Check container logs**
   ```bash
   docker-compose logs -f
   ```

2. **Check Nginx configuration**
   ```bash
   nginx -t
   ```

3. **Check SSL certificate status**
   ```bash
   certbot certificates
   ```

4. **Check service status**
   ```bash
   systemctl status nginx
   docker ps
   ```

## Security Considerations

1. Keep your server updated
2. Use strong passwords for all services
3. Regularly backup your database
4. Monitor your server logs
5. Keep your SSL certificates renewed
