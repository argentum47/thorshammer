# ─────────────────────────────────────────────────────────────────────────────
# ThorsHammer v2.1  —  Dockerfile
# Single Compute Engine deployment (no load balancer required)
# ─────────────────────────────────────────────────────────────────────────────
#
# Local build & run:
#   docker build -t thorshammer .
#   docker run --env-file .env -p 8000:8000 thorshammer
#
# Push to GCP Container Registry:
#   gcloud auth configure-docker
#   docker tag thorshammer gcr.io/YOUR_PROJECT_ID/thorshammer
#   docker push gcr.io/YOUR_PROJECT_ID/thorshammer
#
# GCP Compute Engine — recommended machine for this budget stage:
#   e2-medium  (2 vCPU, 4 GB RAM) — ~$27/month
#   Enough for dozens of concurrent subscribers.  Upgrade to n2-standard-2
#   (~$55/month) when you have 150+ subscribers or want a second CE instance.
#
# One-time GCP CE setup:
#   gcloud compute instances create thorshammer-1 \
#     --image-family=cos-stable \
#     --image-project=cos-cloud \
#     --machine-type=e2-medium \
#     --zone=us-central1-a \
#     --tags=http-server,https-server \
#     --metadata=startup-script='
#       docker pull gcr.io/YOUR_PROJECT_ID/thorshammer
#       docker run -d --restart always \
#         --env-file /etc/thorshammer/.env \
#         -p 8000:8000 \
#         --name thorshammer \
#         gcr.io/YOUR_PROJECT_ID/thorshammer'
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

LABEL maintainer="thorshammer@example.com"
LABEL version="2.1"
LABEL description="ThorsHammer — Lightning/Weather/Wildfire API for Custer County CO"

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY thorshammer_v2_1.py .

RUN mkdir -p backups drone_imagery

RUN useradd --create-home --shell /bin/bash thorshammer && \
    chown -R thorshammer:thorshammer /app
USER thorshammer

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 2 workers matches the 2 vCPUs on an e2-medium.
CMD ["uvicorn", "thorshammer_v2_1:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--log-level", "info", \
     "--access-log"]

