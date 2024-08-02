# Build step #1: build the React front end
FROM node:16-alpine AS build-step
WORKDIR /app
ENV PATH=/app/node_modules/.bin:$PATH
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm install
RUN npm run build

# Build step #2: build the API with the client as static files
FROM python:3.12-slim
WORKDIR /app

# Copy the build artifacts from the first stage
COPY --from=build-step /app/build ./build

# Copy backend files
COPY frontend/backend/requirements.txt ./api/requirements.txt
COPY frontend/backend/wsgi.py ./api/wsgi.py
COPY frontend/backend/.flaskenv ./api/.flaskenv

RUN pip install -r ./api/requirements.txt
ENV FLASK_ENV=production

EXPOSE 5000
WORKDIR /app/api
CMD ["gunicorn", "-b", ":5000", "wsgi:app"]
