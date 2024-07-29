# Use a base image with Node.js
FROM node:18-alpine

# Set the working directory
WORKDIR /app

# Copy package.json and package-lock.json
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy the rest of the application
COPY . .

# Build the React app
RUN npm run build

# Install a simple HTTP server
RUN npm install -g serve

# Serve the app
CMD ["serve", "-s", "build"]

# Expose port 80
EXPOSE 3000
