# Stage 1: Build the React frontend
FROM node:18-alpine AS build-step
WORKDIR /app
ENV PATH /app/node_modules/.bin:$PATH
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Set up the Python backend with the built frontend
FROM python:3.10-slim
WORKDIR /app

# Copy the build artifacts from the first stage
COPY --from=build-step /app/build ./build

# Create the api directory
RUN mkdir -p ./api

# Copy backend files
COPY api/ ./api/

# Install Python dependencies
RUN pip install -r ./api/requirements.txt

# Install Supervisor
RUN pip install supervisor

# Create the log directory for supervisor
RUN mkdir -p /var/log/supervisor

# Copy the supervisord configuration file
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose the application port
EXPOSE 3000

# Start Supervisor
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
