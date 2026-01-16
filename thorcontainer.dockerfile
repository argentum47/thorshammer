# Base Image: Use a lightweight, official Python image
# "slim" removes unnecessary tools to keep the file size small (saving money)
FROM python:3.11-slim

# Environment Variables
# PYTHONDONTWRITEBYTECODE: Prevents Python from writing .pyc files to disk
# PYTHONUNBUFFERED: Ensures logs appear immediately in GCP Cloud Logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Work Directory
WORKDIR /app

# Install Dependencies
# Copy requirements FIRST to leverage Docker cache
# If requirements.txt hasn't changed, Docker skips the "pip install" step on rebuilds
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy Application Code
COPY . .

# Security: Create a non-root user
# Running as root is a security risk. This creates a user named "thor" and switches to it
RUN adduser --disabled-password --gecos "" thor
USER thor

# The Startup Command
# Cloud Run expects the app to listen on port 8080 by default
# "main:app" refers to "main.py" and the "app = FastAPI()" object inside it
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]