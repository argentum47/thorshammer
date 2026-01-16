terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- 1. Enable Required APIs ---
# This ensures you don't get "API not enabled" errors when deploying to a fresh project.
resource "google_project_service" "cloud_run_api" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secret_manager_api" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# --- 2. Create the Secret (Infrastructure only) ---
# We create the "Box" for the secret, but NOT the password value itself.
# This keeps your password out of this text file. 
resource "google_secret_manager_secret" "weatherbit_key" {
  secret_id = "WEATHERBIT_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secret_manager_api]
}

# --- 3. The Cloud Run Service ---
resource "google_cloud_run_v2_service" "thorshammer" {
  name     = "thorshammer-backend"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL" # Allows public internet traffic

  template {
    containers {
      image = var.container_image
      
      # Connect port 8080 (FastAPI default)
      ports {
        container_port = 8080
      }

      # Inject the API Key safely from Secret Manager
      env {
        name = "WEATHERBIT_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.weatherbit_key.secret_id
            version = "latest" # Always pulls the newest version you added
          }
        }
      }
    }
    
    # Scale to Zero settings (Crucial for cost savings)
    scaling {
      min_instance_count = 0
      max_instance_count = 1 # Keep it at 1 for now to prevent runaway bills
    }
  }

  depends_on = [google_project_service.cloud_run_api]
}

# --- 4. Allow Public Access ---
# This makes the API reachable by your mobile app without complex IAM auth.
resource "google_cloud_run_service_iam_member" "public_access" {
  location = google_cloud_run_v2_service.thorshammer.location
  service  = google_cloud_run_v2_service.thorshammer.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- 5. Secret Access Policy ---
# Gives your Cloud Run service permission to actually "read" the secret.
resource "google_secret_manager_secret_iam_member" "secret_access" {
  secret_id = google_secret_manager_secret.weatherbit_key.id
  role      = "roles/secretmanager.secretAccessor"
  # The Cloud Run service identity (Compute Engine default service account)
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

# --- Variables & Data ---
data "google_project" "current" {}

variable "project_id" {
  description = "Your Google Cloud Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region (e.g., us-central1)"
  type        = string
  default     = "us-central1"
}

variable "container_image" {
  description = "The full URL of your Docker image (e.g., gcr.io/PROJECT/IMAGE)"
  type        = string
}

# --- Outputs ---
output "service_url" {
  value = google_cloud_run_v2_service.thorshammer.uri
}