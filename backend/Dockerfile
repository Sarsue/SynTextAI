# backend/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .

# Expose port
EXPOSE 5000

CMD ["gunicorn", "app:create_app", "--bind", "0.0.0.0:5000"]