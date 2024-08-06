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
COPY backend/ ./api/


# Install Python dependencies
RUN pip install -r ./api/requirements.txt

# Expose the application port
EXPOSE 3000

# Start the Flask application
WORKDIR /app/api
CMD ["gunicorn", "-b", ":3000", "wsgi:app"]
