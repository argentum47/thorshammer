"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              T H O R S H A M M E R   v2.1                                    ║
║  Lightning Detection · Severe Weather · Wildfire Drone Recon                 ║
║  Custer County, CO - Single Drone Base: Taylor Rd / Goodwin Creek / 9,000'   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  DEPLOYMENT TARGET                                                           ║
║    Google Cloud Platform — Single Compute Engine (e2-medium)                 ║
║    • One CE instance running Docker + Nginx (no load balancer needed yet)    ║
║    • Nginx handles HTTPS termination and proxies to this FastAPI container   ║
║    • Firebase handles ALL shared state — so upgrading to a second CE later   ║
║      requires zero code changes (just spin up the second container)          ║
║    • Monthly flat rate: $7.00 per Custer County subscriber                   ║
║                                                                              ║
║  WHAT'S NEW IN v2.1                                                          ║
║    • Single Compute Engine (removed dual-CE load balancer overhead)          ║
║    • Monthly price corrected to $7.00                                        ║
║    • Firestore replaces all in-memory lists (stateless from day one)         ║
║    • Firebase Auth JWT middleware — subscriber-only endpoint guard           ║
║    • Stripe webhook activates / cancels Firestore subscriber records         ║
║    • GCP structured JSON logging (Cloud Logging picks it up from stdout)     ║
║    • Single-drone enforcement via Firestore mission status query             ║
║    • Graceful SIGTERM handler for Docker restarts                            ║
║    • nginx.conf and docker-compose.yml updated for single-instance setup     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  FIREBASE & FIRESTORE — WHAT THEY ARE AND HOW WE USE THEM                    ║
║                                                                              ║
║  Firebase is the Google mobile/web backend platform; we use three services:  ║
║                                                                              ║
║  1. Firebase Authentication                                                  ║
║     What it is: A managed login system.  It handles user accounts,           ║
║     passwords, Google Sign-In, Apple Sign-In, etc.  When a subscriber        ║
║     logs in through the FlutterFlow app, Firebase Auth issues a short-lived  ║
║     JSON Web Token (JWT) — a signed string that proves "this is user X".     ║
║     How we use it: Every protected API endpoint calls                        ║
║     verify_firebase_token() which validates that JWT.  If the token is       ║
║     invalid or expired, the endpoint returns HTTP 401 before doing any       ║
║     work.  The FlutterFlow app refreshes tokens automatically.               ║
║                                                                              ║
║  2. Cloud Firestore                                                          ║
║     What it is: A NoSQL cloud database.  Data is stored as "documents"       ║
║     inside "collections" — similar to JSON objects inside named folders.     ║
║     It's not a traditional rows-and-columns SQL database; instead each       ║
║     document is a flexible key-value map.  Firestore is real-time capable    ║
║     (the FlutterFlow app can subscribe to live updates) and scales           ║
║     automatically.                                                           ║
║                                                                              ║
║     How we use it — four collections:                                        ║
║       subscribers/   one document per user, keyed by Firebase Auth UID       ║
║                      fields: email, active, stripe_status, fcm_token,        ║
║                              period_end, updated_at                          ║
║                      Purpose: know who has a paid subscription               ║
║                                                                              ║
║       weather_records/  one document per /check-risk call                    ║
║                      fields: all WeatherReport fields + timestamp            ║
║                      Purpose: history tab in the app, billing audit trail    ║
║                                                                              ║
║       drone_missions/  one document per dispatched mission, keyed by         ║
║                      mission_id  (e.g. RECON-20250301-143022)                ║
║                      fields: status, waypoints, camera, safety, images[]     ║
║                      Purpose: the FlutterFlow operator screen polls this;    ║
║                               both the backend and the app read/write it     ║
║                                                                              ║
║       daily_backups/   one document per calendar day                         ║
║                      fields: record_count, mission_count, generated_at       ║
║                      Purpose: lightweight daily summary / ops dashboard      ║
║                                                                              ║
║     Why not just use a local SQLite file or in-memory lists?                 ║
║     Because those live inside the Docker container.  When Docker restarts    ║
║     the container (OS patch, crash, redeploy) ALL that data is gone.         ║
║     Firestore lives outside the container — a restart loses nothing.         ║
║     When you eventually add a second CE instance, both containers read       ║
║     the same Firestore data automatically with zero code changes.            ║
║                                                                              ║
║  3. Firebase Cloud Messaging (FCM)                                           ║
║     What it is: Google's push notification service.  It delivers alerts      ║
║     to Android and iOS devices even when the app is in the background.       ║
║     How we use it: When fire risk hits HIGH/EXTREME or dry lightning is      ║
║     detected, broadcast_alert_to_subscribers() looks up every active         ║
║     subscriber's stored FCM token in Firestore and sends each one a          ║
║     push notification.  The FlutterFlow app stores its own FCM token         ║
║     by calling /auth/register-fcm-token on first launch.                     ║
║                                                                              ║
║  All three services share one Firebase project and one service-account       ║
║  JSON credentials file.  One setup — three capabilities.                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  FLUTTERFLOW API CALL MAP                                                    ║
║    POST /auth/verify-subscription      → app launch gate (paid or paywall)   ║
║    POST /auth/register-fcm-token       → store push token on first launch    ║
║    GET  /base-station                  → home screen weather tile            ║
║    POST /check-risk                    → user-location risk card             ║
║    GET  /lightning-strikes             → lightning map overlay               ║
║    GET  /weather/history               → history list screen                 ║
║    POST /billing/create-checkout-session → Stripe payment URL                ║
║    GET  /drone/missions/pending        → operator dispatch screen            ║
║    POST /drone/dispatch                → manual recon trigger                ║
║    POST /drone/missions/{id}/acknowledge  → MSDK picked up mission           ║
║    POST /drone/missions/{id}/complete     → mission finished                 ║
║    POST /drone/missions/{id}/abort        → emergency stop                   ║
║    POST /drone/upload-image            → drone camera image receiver         ║
║    POST /notify                        → operator push (internal)            ║
║    GET  /health                        → uptime monitor probe                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ─── Standard Library ────────────────────────────────────────────────────────
import os
import json
import time
import signal
import asyncio
import logging
import threading
import socket
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

# ─── Third-Party ─────────────────────────────────────────────────────────────
import requests
import schedule
from dotenv import load_dotenv
from fastapi import (
    FastAPI, HTTPException, Header, BackgroundTasks,
    UploadFile, File, Request
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

load_dotenv()

# ── Weatherbit ────────────────────────────────────────────────────────────────
WEATHERBIT_KEY  = os.getenv("WEATHERBIT_API_KEY")
WEATHERBIT_BASE = "https://api.weatherbit.io/v2.0"

# ── Firebase / GCP ────────────────────────────────────────────────────────────
# FIREBASE_CREDENTIALS_PATH: path to the service-account JSON you download from
# Firebase Console → Project Settings → Service Accounts → Generate New Private Key
FIREBASE_CREDS  = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
GCP_PROJECT_ID  = os.getenv("GCP_PROJECT_ID", "thorshammer")

# ── Stripe ────────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID       = os.getenv("STRIPE_PRICE_ID")
MONTHLY_PRICE_USD     = 7.00        # ← flat monthly rate for Custer County subscribers

# ── Instance identity ─────────────────────────────────────────────────────────
# socket.gethostname() returns the Docker container ID on GCP, which is unique
# per container restart — useful for log tracing.
INSTANCE_ID = socket.gethostname()

# ── Drone Base Station ────────────────────────────────────────────────────────
# Physical location: top of Taylor Rd on the eastern slope of Venable Mountain,
# Custer County, CO.  Verify coordinates on-site with GPS before first flight.
# Elevation matters: DJI altitude limits are AGL (above ground level), so the
# SDK needs BASE_STATION_ELEV_M to compute safe flight ceilings at ~9,800 ft.
BASE_STATION_NAME   = "Taylor Rd / Venable Mountain, Custer County, CO"
BASE_STATION_LAT    = 38.1067
BASE_STATION_LON    = -105.6089
BASE_STATION_ELEV_M = 2987         # approx. 9,800 ft — confirm with topo survey

# ── Drone Limits ──────────────────────────────────────────────────────────────
DRONE_RECON_RADIUS_KM = 10.0       # Part 107 VLOS envelope until BVLOS cert
DRONE_MAX_WIND_ABORT  = 12.0       # m/s (~27 mph) — hard abort threshold
DRONE_RTH_BATTERY_PCT = 30         # % — return-to-home trigger
DRONE_MODEL           = "DJI Mavic Pro 4"

# ═════════════════════════════════════════════════════════════════════════════
# GCP STRUCTURED LOGGING
# ─────────────────────────────────────────────────────────────────────────────
# On GCP Compute Engine, Docker forwards container stdout to Cloud Logging.
# Formatting log lines as JSON means Cloud Logging can parse severity, timestamp,
# and custom fields automatically — no log agent or extra library needed.
# View logs: GCP Console → Logging → Log Explorer → resource: gce_instance
# ═════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format=(
        '{"time":"%(asctime)s","severity":"%(levelname)s",'
        '"logger":"%(name)s","message":"%(message)s",'
        '"instance":"' + INSTANCE_ID + '"}'
    ),
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("ThorsHammer")

if not WEATHERBIT_KEY:
    logger.warning("WEATHERBIT_API_KEY not set — weather calls will fail.")

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE ADMIN SDK  (Firestore + Auth + FCM)
# ─────────────────────────────────────────────────────────────────────────────
# The firebase-admin SDK is Google's server-side library.  It is different from
# the Firebase client SDK that runs inside your FlutterFlow app.
#
#   Client SDK (in FlutterFlow) — used by subscribers on their phones:
#     signs users in, gets the ID token, listens to Firestore in real time
#
#   Admin SDK (this file) — used by your server:
#     verifies ID tokens, reads/writes Firestore, sends FCM push notifications
#     bypasses security rules because it uses a privileged service-account key
#
# Setup steps (one-time):
#   1. firebase.google.com → create project "thorshammer"
#   2. Project Settings → Service Accounts → Generate New Private Key
#   3. Save the downloaded JSON as firebase_credentials.json next to this file
#      (or set FIREBASE_CREDENTIALS_PATH in .env to point elsewhere)
#   4. Firestore Database → Create database → Native mode → us-central1
#   5. Authentication → Sign-in method → enable Email/Password + Google
#   6. Project Settings → Cloud Messaging → note your Server Key (for FCM)
# ═════════════════════════════════════════════════════════════════════════════

firebase_app  = None
firestore_db  = None
fcm_available = False

try:
    import firebase_admin
    from firebase_admin import (
        credentials, firestore,
        messaging as fcm_messaging,
        auth as fb_auth
    )

    if os.path.exists(FIREBASE_CREDS):
        cred          = credentials.Certificate(FIREBASE_CREDS)
        firebase_app  = firebase_admin.initialize_app(cred)
        # firestore.client() returns a handle to your Firestore database.
        # All reads/writes in this file go through this client object.
        firestore_db  = firestore.client()
        fcm_available = True
        logger.info("Firebase Admin SDK ready (Firestore + Auth + FCM).")
    else:
        logger.warning(
            f"firebase_credentials.json not found at '{FIREBASE_CREDS}'. "
            "Firestore, Auth, and FCM are disabled. "
            "Download the service-account JSON from the Firebase console."
        )
except ImportError:
    logger.warning(
        "firebase-admin not installed. "
        "Run: pip install firebase-admin --break-system-packages"
    )

# ═════════════════════════════════════════════════════════════════════════════
# STRIPE  (monthly subscription fee)
# ─────────────────────────────────────────────────────────────────────────────
# Setup steps (one-time):
#   1. stripe.com → create account
#   2. Dashboard → Products → Add Product → "ThorsHammer Monthly"
#      → Add Price → $7.00 USD recurring monthly
#   3. Copy the Price ID (price_xxx) → STRIPE_PRICE_ID in .env
#   4. Dashboard → Developers → Webhooks → Add endpoint:
#        URL: https://YOUR_DOMAIN/billing/stripe-webhook
#        Events: customer.subscription.created
#                customer.subscription.deleted
#                invoice.paid
#   5. Copy Signing Secret → STRIPE_WEBHOOK_SECRET in .env
# ═════════════════════════════════════════════════════════════════════════════

stripe_available = False
try:
    import stripe as stripe_lib
    if STRIPE_SECRET_KEY:
        stripe_lib.api_key = STRIPE_SECRET_KEY
        stripe_available   = True
        logger.info("Stripe billing ready ($7.00/mo).")
    else:
        logger.warning("STRIPE_SECRET_KEY not set — billing endpoints inactive.")
except ImportError:
    logger.warning(
        "stripe not installed. "
        "Run: pip install stripe --break-system-packages"
    )

# ═════════════════════════════════════════════════════════════════════════════
# PYDANTIC DATA MODELS
# Each model maps to a FlutterFlow API Call response type.
# In FlutterFlow: API Calls → Add Call → Response tab → Import from JSON
# ═════════════════════════════════════════════════════════════════════════════

class Coordinates(BaseModel):
    latitude:  float = Field(..., ge=-90,  le=90)
    longitude: float = Field(..., ge=-180, le=180)

class WeatherReport(BaseModel):
    """Primary response model — returned by /check-risk and /base-station."""
    location:               str
    latitude:               float
    longitude:              float
    timestamp:              str
    temperature_c:          Optional[float] = None
    humidity_pct:           Optional[float] = None
    wind_speed_ms:          Optional[float] = None
    wind_direction:         Optional[str]   = None
    precip_mm_hr:           Optional[float] = None
    cloud_cover_pct:        Optional[float] = None
    condition_code:         Optional[int]   = None
    condition_description:  Optional[str]   = None
    condition:              str
    fire_risk_score:        int              # 0–100
    fire_risk_level:        str              # LOW · MODERATE · HIGH · EXTREME
    lightning_nearby:       bool
    dry_lightning:          bool             # lightning + no rain = top ignition risk
    active_alerts:          list
    drone_recon_recommended: bool
    base_station_name:      str = BASE_STATION_NAME

class SubscriberStatus(BaseModel):
    """Returned by /auth/verify-subscription — tells the app which screen to show."""
    uid:           str
    email:         Optional[str] = None
    display_name:  Optional[str] = None
    active:        bool              # True = paid and current → show main app
    plan:          str = "monthly_flat_7usd"
    county:        str = "Custer County, CO"
    stripe_status: Optional[str] = None
    expires_at:    Optional[str] = None

class DroneCommand(BaseModel):
    mission_type:         str   = "fire_recon"
    target_lat:           Optional[float] = None
    target_lon:           Optional[float] = None
    altitude_m:           float = Field(default=80.0, ge=10, le=120)
    capture_interval_sec: int   = Field(default=30, ge=5, le=300)
    notes:                Optional[str] = None

class AlertPayload(BaseModel):
    device_token: str
    title:        str
    body:         str
    data:         Optional[dict] = None

class CheckoutRequest(BaseModel):
    """Sent by the FlutterFlow "Subscribe" button action."""
    uid:         str
    email:       str
    success_url: str = "https://thorshammer.app/subscribe/success"
    cancel_url:  str = "https://thorshammer.app/subscribe/cancel"

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE AUTH MIDDLEWARE
# ─────────────────────────────────────────────────────────────────────────────
# How the token flow works end-to-end:
#
#   1. Subscriber opens the FlutterFlow app and signs in with Google SSO.
#      Firebase client SDK handles this entirely on-device.
#
#   2. After login, the FlutterFlow app calls:
#        FirebaseAuth.instance.currentUser!.getIdToken()
#      This returns a JWT string — a signed, base64-encoded blob that contains
#      the user's UID, email, and an expiry timestamp (~1 hour).
#
#   3. The app includes that token in every API request header:
#        Authorization: Bearer eyJhbGci...
#
#   4. This function verifies the signature against Firebase Auth's public keys.
#      If valid → returns the decoded payload (uid, email, etc.)
#      If invalid/expired → raises HTTP 401
#
#   5. The app automatically refreshes tokens before expiry — you don't need
#      to handle token renewal in FlutterFlow manually.
# ═════════════════════════════════════════════════════════════════════════════

async def verify_firebase_token(authorization: Optional[str] = Header(None)) -> dict:
    """
    Inject as a dependency into any endpoint that requires a logged-in user.
    Example usage in an endpoint:

        @app.get("/protected")
        async def my_endpoint(authorization: Optional[str] = Header(None)):
            token = await verify_firebase_token(authorization)
            uid = token["uid"]   # now you know exactly who is calling
    """
    if not firebase_app:
        # Firebase not yet configured — allow through in development mode only
        if os.getenv("ENV", "development") == "production":
            raise HTTPException(status_code=503, detail="Auth service unavailable.")
        return {"uid": "dev-user", "email": "dev@thorshammer.local"}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")

    id_token = authorization.split("Bearer ", 1)[1]
    try:
        return fb_auth.verify_id_token(id_token)
    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

async def require_active_subscription(uid: str) -> bool:
    """
    Checks Firestore subscribers/{uid}.active == True.
    Firestore path:  /subscribers/<firebase_uid>
    Document fields: { active: bool, stripe_status: str, period_end: str, ... }
    """
    if not firestore_db:
        return True   # allow through when Firestore isn't configured (dev mode)

    # .document(uid).get() fetches one Firestore document by its ID.
    # The document ID is the Firebase Auth UID — same string in both systems.
    doc = firestore_db.collection("subscribers").document(uid).get()
    if not doc.exists:
        return False
    return doc.to_dict().get("active", False)

# ═════════════════════════════════════════════════════════════════════════════
# WEATHER LOGIC
# ═════════════════════════════════════════════════════════════════════════════

def derive_condition(wd: dict) -> str:
    desc   = wd.get('weather', {}).get('description', '').lower()
    temp_c = wd.get('temp')   or 20
    precip = wd.get('precip') or 0
    clouds = wd.get('clouds') or 0

    if any(t in desc for t in ['thunderstorm', 'tornado', 'hurricane', 'extreme']):
        return "Severe Weather"
    if 'lightning' in desc or 'electrical' in desc:
        return "Lightning Activity"
    if 'rain' in desc or 'drizzle' in desc or precip > 0.1:
        return "Rainy / Precipitating"
    if 'snow' in desc:
        return "Snowy"
    if 'cloud' in desc or clouds > 75:
        return "Overcast"
    if clouds > 25:
        return "Partly Cloudy"
    if temp_c > 25: return "Clear and Warm"
    if temp_c < 0:  return "Clear and Cold"
    return "Clear"

def calculate_fire_risk(wd: dict) -> tuple[int, str]:
    """
    Weighted fire risk score (0–100) tuned for Colorado high-altitude desert.
    Humidity 35 pts · Temperature 25 pts · Wind 25 pts · Precipitation 10 pts
    Thunderstorm with heavy rain: -10 adjustment
    """
    rh     = wd.get('rh')       or 50
    temp_c = wd.get('temp')     or 20
    wind   = wd.get('wind_spd') or 0
    precip = wd.get('precip')   or 0
    desc   = wd.get('weather', {}).get('description', '').lower()
    score  = 0

    if rh < 10:    score += 35
    elif rh < 15:  score += 28
    elif rh < 25:  score += 20
    elif rh < 35:  score += 10

    if temp_c > 38:   score += 25
    elif temp_c > 32: score += 18
    elif temp_c > 27: score += 10
    elif temp_c > 22: score += 5

    if wind > 12:  score += 25
    elif wind > 8: score += 18
    elif wind > 5: score += 10
    elif wind > 3: score += 5

    if precip < 0.01:   score += 10
    elif precip < 0.10: score += 5

    if 'thunderstorm' in desc and precip > 0.5:
        score = max(score - 10, 0)

    score = min(score, 100)
    level = ("EXTREME" if score >= 75 else "HIGH" if score >= 50
             else "MODERATE" if score >= 25 else "LOW")
    return score, level

def detect_lightning(wd: dict, alerts: list, strike_count: int) -> tuple[bool, bool]:
    """Returns (lightning_nearby, dry_lightning). Dry = highest ignition risk."""
    desc   = wd.get('weather', {}).get('description', '').lower()
    precip = wd.get('precip') or 0
    terms  = ['thunderstorm', 'lightning', 'electrical storm', 't-storm']

    lightning = any(t in desc for t in terms) or strike_count > 0
    if not lightning:
        for a in alerts:
            txt = (a.get('title', '') + a.get('description', '')).lower()
            if any(t in txt for t in terms):
                lightning = True
                break

    return lightning, (lightning and precip < 0.1)

# ═════════════════════════════════════════════════════════════════════════════
# WEATHERBIT API HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def fetch_current_weather(lat: float, lon: float) -> Optional[dict]:
    if not WEATHERBIT_KEY:
        return None
    try:
        r = requests.get(
            f"{WEATHERBIT_BASE}/current",
            params={"lat": lat, "lon": lon, "key": WEATHERBIT_KEY, "units": "M"},
            timeout=15,
        )
        r.raise_for_status()
        records = r.json().get('data', [])
        return records[0] if records else None
    except requests.exceptions.RequestException as e:
        logger.error(f"Weatherbit /current: {e}")
        return None

def fetch_active_alerts(lat: float, lon: float) -> list:
    if not WEATHERBIT_KEY:
        return []
    try:
        r = requests.get(
            f"{WEATHERBIT_BASE}/alerts",
            params={"lat": lat, "lon": lon, "key": WEATHERBIT_KEY},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get('alerts', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Weatherbit /alerts: {e}")
        return []

def fetch_lightning_strikes(lat: float, lon: float, radius_km: float = 50) -> list:
    """
    ⚠️  Requires Weatherbit Pro+ paid plan (HTTP 403 on free tier).
        Upgrade: https://www.weatherbit.io/pricing
    """
    if not WEATHERBIT_KEY:
        return []
    try:
        r = requests.get(
            f"{WEATHERBIT_BASE}/lightning",
            params={"lat": lat, "lon": lon, "radius": radius_km, "key": WEATHERBIT_KEY},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get('data', [])
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            logger.warning(
                "Weatherbit Lightning API requires Pro+ plan. "
                "Upgrade: https://www.weatherbit.io/pricing"
            )
        else:
            logger.error(f"Weatherbit /lightning HTTP error: {e}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Weatherbit /lightning: {e}")
    return []

# ═════════════════════════════════════════════════════════════════════════════
# DRONE CONTROLLER  —  DJI Mavic Pro 4
# ─────────────────────────────────────────────────────────────────────────────
# Mission Queue Architecture (DJI MSDK v5 / FlutterFlow integration):
#
#   This backend writes mission documents to Firestore.
#   The FlutterFlow operator tablet at the base station polls
#   GET /drone/missions/pending every 30 s via a Timer widget.
#   When a mission appears, the operator app executes it through
#   the DJI Mobile SDK v5 Flutter plugin (custom FlutterFlow action).
#
#   Single-drone enforcement: before queuing a new auto-dispatch,
#   the controller queries Firestore for any mission in an active
#   status.  If one exists, the new dispatch is blocked.
#   Operator manual dispatches bypass this with operator_override=True.
# ═════════════════════════════════════════════════════════════════════════════

class DroneController:

    ACTIVE_STATUSES = {"QUEUED", "ACKNOWLEDGED", "IN_FLIGHT"}

    def _mission_doc(self, mid: str):
        if firestore_db:
            return firestore_db.collection("drone_missions").document(mid)
        return None

    def _next_id(self) -> str:
        return f"RECON-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    def _drone_available(self) -> bool:
        """Returns False if any mission is currently active (single-drone guard)."""
        if not firestore_db:
            return True
        # Firestore .where() filters documents by field value.
        # "in" operator matches any value in the provided list.
        active = (
            firestore_db.collection("drone_missions")
            .where("status", "in", list(self.ACTIVE_STATUSES))
            .limit(1)
            .stream()
        )
        return not any(True for _ in active)

    def dispatch_recon(
        self,
        target_lat:           float,
        target_lon:           float,
        fire_risk_level:      str,
        lightning_nearby:     bool,
        altitude_m:           float = 80.0,
        capture_interval_sec: int   = 30,
        operator_override:    bool  = False,
    ) -> dict:

        if lightning_nearby:
            logger.warning("Drone BLOCKED — active lightning within 50 km.")
            return {
                "mission_id": None,
                "status":     "BLOCKED_LIGHTNING",
                "reason":     "Active lightning — drone grounded for safety.",
            }

        if not operator_override and not self._drone_available():
            logger.info("Drone BLOCKED — mission already active.")
            return {
                "mission_id": None,
                "status":     "BLOCKED_ACTIVE_MISSION",
                "reason":     "Drone already on a mission. One concurrent mission allowed.",
            }

        mid = self._next_id()
        mission = {
            "mission_id":   mid,
            "status":       "QUEUED",
            "created_at":   datetime.now(timezone.utc).isoformat(),
            "drone_model":  DRONE_MODEL,
            "base_station": BASE_STATION_NAME,
            "trigger": {
                "fire_risk_level":   fire_risk_level,
                "lightning_nearby":  lightning_nearby,
                "operator_override": operator_override,
            },
            "waypoints": [
                {
                    "order":       1,
                    "lat":         BASE_STATION_LAT,
                    "lon":         BASE_STATION_LON,
                    "altitude_m":  15,
                    "action":      "takeoff",
                },
                {
                    "order":                2,
                    "lat":                  target_lat,
                    "lon":                  target_lon,
                    "altitude_m":           altitude_m,
                    "action":               "orbit_and_capture",
                    "orbit_radius_m":       200,
                    "capture_interval_sec": capture_interval_sec,
                    "orbit_loops":          2,
                },
                {
                    "order":      3,
                    "lat":        BASE_STATION_LAT,
                    "lon":        BASE_STATION_LON,
                    "altitude_m": 15,
                    "action":     "land",
                },
            ],
            "camera": {
                "mode":             "photo_and_video",
                "video_resolution": "4K_30fps",
                "photo_format":     "JPEG+RAW",
                "thermal_imaging":  False,  # set True if Zenmuse XT2 thermal camera attached
            },
            "safety": {
                "max_wind_abort_ms":     DRONE_MAX_WIND_ABORT,
                "rth_battery_pct":       DRONE_RTH_BATTERY_PCT,
                "geofence_radius_km":    DRONE_RECON_RADIUS_KM,
                "abort_if_lightning_km": 5.0,
                "lost_signal_behavior":  "return_to_home",
                "base_elevation_m":      BASE_STATION_ELEV_M,
            },
        }

        # .set() creates or overwrites the Firestore document at drone_missions/{mid}
        ref = self._mission_doc(mid)
        if ref:
            ref.set(mission)
        logger.info(f"🚁 Mission queued: {mid}  risk={fire_risk_level}")
        return mission

    def _update_status(self, mid: str, status: str, extra: dict = None) -> bool:
        ref = self._mission_doc(mid)
        if ref and ref.get().exists:
            # .update() merges fields into an existing Firestore document
            # without overwriting the entire document.
            update = {
                "status": status,
                f"{status.lower()}_at": datetime.now(timezone.utc).isoformat(),
            }
            if extra:
                update.update(extra)
            ref.update(update)
            return True
        return False

    def get_pending(self) -> list:
        if not firestore_db:
            return []
        docs = (
            firestore_db.collection("drone_missions")
            .where("status", "==", "QUEUED")
            .stream()
        )
        return [d.to_dict() for d in docs]

    def acknowledge(self, mid: str) -> bool:
        return self._update_status(mid, "ACKNOWLEDGED")

    def complete(self, mid: str) -> bool:
        return self._update_status(mid, "COMPLETED")

    def abort(self, mid: str, reason: str = "operator abort") -> bool:
        return self._update_status(mid, "ABORTED", {"abort_reason": reason})

    def get_log(self, limit: int = 50) -> list:
        if not firestore_db:
            return []
        docs = (
            firestore_db.collection("drone_missions")
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]

drone = DroneController()

# ═════════════════════════════════════════════════════════════════════════════
# PUSH NOTIFICATIONS  (Firebase Cloud Messaging → FlutterFlow app)
# ─────────────────────────────────────────────────────────────────────────────
# FCM token lifecycle:
#   1. On first app launch, the FlutterFlow app calls:
#        FirebaseMessaging.instance.getToken()
#      This returns a device-specific token string (~150 chars).
#   2. The app posts that token to /auth/register-fcm-token.
#   3. This backend stores it in Firestore subscribers/{uid}.fcm_token.
#   4. When an alert fires, broadcast_alert_to_subscribers() reads all
#      active subscriber documents, pulls fcm_token from each, and sends.
#   5. FCM delivers to the device regardless of whether the app is open.
# ═════════════════════════════════════════════════════════════════════════════

def send_push_to_token(
    device_token: str, title: str, body: str, data: dict = None
) -> bool:
    if not fcm_available:
        logger.warning("Push skipped — Firebase not configured.")
        return False
    try:
        msg = fcm_messaging.Message(
            notification=fcm_messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            token=device_token,
            android=fcm_messaging.AndroidConfig(priority="high"),
            apns=fcm_messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=fcm_messaging.APNSPayload(
                    aps=fcm_messaging.Aps(sound="default")
                ),
            ),
        )
        resp = fcm_messaging.send(msg)
        logger.info(f"FCM sent: {resp}")
        return True
    except Exception as e:
        logger.error(f"FCM error: {e}")
        return False

def broadcast_alert_to_subscribers(
    title: str, body: str, data: dict = None
) -> int:
    """
    Pushes an alert to every active subscriber who has a stored FCM token.
    Called automatically by monitor_base_station() when risk escalates.
    """
    if not firestore_db:
        return 0
    # Firestore compound query: active == True AND fcm_token is not empty
    docs = (
        firestore_db.collection("subscribers")
        .where("active",     "==", True)
        .where("fcm_token",  "!=", "")
        .stream()
    )
    count = 0
    for doc in docs:
        token = doc.to_dict().get("fcm_token")
        if token and send_push_to_token(token, title, body, data):
            count += 1
    logger.info(f"Broadcast sent to {count} subscriber(s).")
    return count

# ═════════════════════════════════════════════════════════════════════════════
# CORE ASSESSMENT  (single source of truth for all weather endpoints)
# ═════════════════════════════════════════════════════════════════════════════

def assess_location(lat: float, lon: float) -> dict:
    wd = fetch_current_weather(lat, lon)
    if not wd:
        return {}
    alerts          = fetch_active_alerts(lat, lon)
    strikes         = fetch_lightning_strikes(lat, lon, 50)
    fire_score, fire_level = calculate_fire_risk(wd)
    lightning, dry  = detect_lightning(wd, alerts, len(strikes))
    precip          = wd.get('precip') or 0
    drone_rec       = (fire_level in ("HIGH", "EXTREME") and precip < 0.5) or dry

    return {
        "weather":          wd,
        "alerts":           alerts,
        "strikes":          strikes,
        "fire_score":       fire_score,
        "fire_level":       fire_level,
        "lightning_nearby": lightning,
        "dry_lightning":    dry,
        "drone_recommended": drone_rec,
        "precip":           precip,
    }

def _build_report(lat: float, lon: float, ctx: dict) -> WeatherReport:
    wd = ctx["weather"]
    return WeatherReport(
        location=f"{lat:.4f},{lon:.4f}",
        latitude=lat,
        longitude=lon,
        timestamp=wd.get('datetime', datetime.now(timezone.utc).isoformat()),
        temperature_c=wd.get('temp'),
        humidity_pct=wd.get('rh'),
        wind_speed_ms=wd.get('wind_spd'),
        wind_direction=wd.get('wind_cdir_full'),
        precip_mm_hr=wd.get('precip'),
        cloud_cover_pct=wd.get('clouds'),
        condition_code=wd.get('weather', {}).get('code'),
        condition_description=wd.get('weather', {}).get('description'),
        condition=derive_condition(wd),
        fire_risk_score=ctx["fire_score"],
        fire_risk_level=ctx["fire_level"],
        lightning_nearby=ctx["lightning_nearby"],
        dry_lightning=ctx["dry_lightning"],
        active_alerts=ctx["alerts"],
        drone_recon_recommended=ctx["drone_recommended"],
    )

def _persist_weather_record(report: WeatherReport) -> None:
    """
    Writes one WeatherReport to Firestore collection 'weather_records'.
    .add() auto-generates a document ID (a random Firestore string like Xk3pQ2...).
    This is fine for append-only logs where you don't need a predictable ID.
    """
    if not firestore_db:
        return
    try:
        firestore_db.collection("weather_records").add(report.model_dump())
    except Exception as e:
        logger.error(f"Firestore write failed: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND MONITOR  (15-min base station self-check)
# ═════════════════════════════════════════════════════════════════════════════

async def monitor_base_station() -> None:
    logger.info(f"🔍 Auto-check: {BASE_STATION_NAME}")
    ctx = assess_location(BASE_STATION_LAT, BASE_STATION_LON)
    if not ctx:
        return

    fl = ctx["fire_level"]
    ln = ctx["lightning_nearby"]
    dl = ctx["dry_lightning"]
    dr = ctx["drone_recommended"]

    logger.info(
        f"  Fire={fl}({ctx['fire_score']})  "
        f"Lightning={ln}  DryLightning={dl}  DroneRec={dr}"
    )

    if dr:
        drone.dispatch_recon(BASE_STATION_LAT, BASE_STATION_LON, fl, ln)

    if fl in ("HIGH", "EXTREME") or dl:
        level_label = "⚡ DRY LIGHTNING" if dl else f"🔥 Fire Risk: {fl}"
        broadcast_alert_to_subscribers(
            title=f"ThorsHammer Alert — {fl}",
            body=(
                f"{level_label} detected near {BASE_STATION_NAME}. "
                "Stay weather aware."
            ),
            data={
                "fire_risk":    fl,
                "dry_lightning": str(dl),
                "lat":          str(BASE_STATION_LAT),
                "lon":          str(BASE_STATION_LON),
            },
        )

def _run_scheduler() -> None:
    """Daemon thread — runs schedule jobs without blocking the async event loop."""
    while True:
        schedule.run_pending()
        time.sleep(30)

# ═════════════════════════════════════════════════════════════════════════════
# GRACEFUL SHUTDOWN  (Docker stop / GCP maintenance)
# ─────────────────────────────────────────────────────────────────────────────
# Docker sends SIGTERM before killing the container.  We catch it and let
# FastAPI finish in-flight requests cleanly before the process exits.
# ═════════════════════════════════════════════════════════════════════════════

_shutdown = threading.Event()

def _sigterm_handler(signum, frame):
    logger.info("SIGTERM — graceful shutdown initiated.")
    _shutdown.set()

signal.signal(signal.SIGTERM, _sigterm_handler)

# ═════════════════════════════════════════════════════════════════════════════
# DAILY SUMMARY  (Firestore snapshot at 23:59)
# ═════════════════════════════════════════════════════════════════════════════

def _save_daily_summary() -> None:
    if not firestore_db:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        rec_count  = sum(1 for _ in (
            firestore_db.collection("weather_records")
            .where("timestamp", ">=", today)
            .stream()
        ))
        mis_count  = sum(1 for _ in (
            firestore_db.collection("drone_missions")
            .where("created_at", ">=", today)
            .stream()
        ))
        # Use document ID = date string so each day's summary is easy to look up:
        # firestore_db.collection("daily_backups").document("2025-06-01").get()
        firestore_db.collection("daily_backups").document(today).set({
            "date":          today,
            "record_count":  rec_count,
            "mission_count": mis_count,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "instance_id":   INSTANCE_ID,
        })
        logger.info(f"✅ Daily summary written for {today}")
    except Exception as e:
        logger.error(f"Daily summary error: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ═════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"⚡ ThorsHammer v2.01 starting — instance {INSTANCE_ID}")
    schedule.every(15).minutes.do(
        lambda: asyncio.get_event_loop().run_in_executor(
            None, asyncio.run, monitor_base_station()
        )
    )
    schedule.every().day.at("23:59").do(_save_daily_summary)
    threading.Thread(target=_run_scheduler, daemon=True, name="scheduler").start()
    logger.info("Scheduler online (monitor every 15 min · daily summary at 23:59)")
    yield
    logger.info("ThorsHammer v2.01 shutting down.")


app = FastAPI(
    title="ThorsHammer",
    description=(
        "Lightning detection · severe weather alerting · wildfire drone recon "
        "for Custer County, CO.  $7/month flat-rate subscription."
    ),
    version="2.0.1",
    lifespan=lifespan,
)

# CORS: allow the FlutterFlow app (and any future web dashboard) to call this API.
# Replace "*" with your domain(s) before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── System ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """
    Nginx upstream health probe and uptime monitor target.
    Nginx marks the backend down if this returns anything other than 2xx.
    Also useful for confirming all services initialized correctly after deploy.
    """
    return {
        "status":      "ok",
        "version":     "2.0.1",
        "instance_id": INSTANCE_ID,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "services": {
            "firebase":   firebase_app  is not None,
            "firestore":  firestore_db  is not None,
            "fcm":        fcm_available,
            "stripe":     stripe_available,
            "weatherbit": WEATHERBIT_KEY is not None,
        },
    }

# ─── Auth & Subscription ──────────────────────────────────────────────────────

@app.post("/auth/verify-subscription", response_model=SubscriberStatus, tags=["Auth"])
async def verify_subscription(authorization: Optional[str] = Header(None)):
    """
    Call at FlutterFlow app launch to determine which screen to show.

    Returns active=True  → route to main app
    Returns active=False → route to $7/mo subscription paywall

    FlutterFlow setup:
      1. Wrap in an "On Page Load" action on your splash screen.
      2. Pass Firebase ID token in the Authorization header via a Custom Header
         in the FlutterFlow API Call definition.
      3. Add a Conditional widget: if response.active == true → navigate to
         HomeScreen, else → navigate to SubscribeScreen.
    """
    token = await verify_firebase_token(authorization)
    uid   = token.get("uid")

    if not firestore_db:
        return SubscriberStatus(uid=uid, active=True, stripe_status="dev_bypass")

    doc = firestore_db.collection("subscribers").document(uid).get()
    if not doc.exists:
        return SubscriberStatus(
            uid=uid,
            email=token.get("email"),
            active=False,
            stripe_status="no_subscription",
        )

    data = doc.to_dict()
    return SubscriberStatus(
        uid=uid,
        email=data.get("email", token.get("email")),
        display_name=data.get("display_name"),
        active=data.get("active", False),
        stripe_status=data.get("stripe_status"),
        expires_at=data.get("period_end"),
    )

@app.post("/auth/register-fcm-token", tags=["Auth"])
async def register_fcm_token(
    token_payload:  dict,
    authorization:  Optional[str] = Header(None),
):
    """
    Called by the FlutterFlow app when a new FCM push token is issued.
    Stores it in Firestore so the backend can push alerts to this device.

    FlutterFlow setup:
      Call on app initialisation using FirebaseMessaging.instance.getToken()
      from the firebase_messaging Flutter package (add as a Custom Action).
      Body: { "fcm_token": "<device token string>" }
    """
    decoded   = await verify_firebase_token(authorization)
    uid       = decoded.get("uid")
    fcm_token = token_payload.get("fcm_token")

    if not fcm_token:
        raise HTTPException(status_code=400, detail="fcm_token is required.")

    if firestore_db:
        # merge=True means we only update fcm_token without touching other fields
        firestore_db.collection("subscribers").document(uid).set(
            {
                "fcm_token":  fcm_token,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True,
        )
    return {"status": "stored", "uid": uid}

# ─── Billing ──────────────────────────────────────────────────────────────────

@app.post("/billing/create-checkout-session", tags=["Billing"])
async def create_checkout_session(req: CheckoutRequest):
    """
    Returns a Stripe-hosted payment URL for the $7/month plan.

    FlutterFlow flow:
      User taps "Subscribe — $7/mo" button
      → App calls this endpoint
      → Response contains { checkout_url: "https://checkout.stripe.com/..." }
      → FlutterFlow opens that URL with the Launch URL action
      → Stripe processes payment, redirects to success_url
      → Stripe fires webhook → /billing/stripe-webhook
      → Firestore sets subscribers/{uid}.active = true
      → User returns to app; /auth/verify-subscription now returns active=true
    """
    if not stripe_available:
        raise HTTPException(status_code=503, detail="Billing service not configured.")
    if not STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="STRIPE_PRICE_ID not set in .env.")

    try:
        session = stripe_lib.checkout.Session.create(
            customer_email=req.email,
            client_reference_id=req.uid,
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            metadata={"firebase_uid": req.uid, "county": "Custer County, CO"},
            success_url=req.success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=req.cancel_url,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        logger.error(f"Stripe session error: {e}")
        raise HTTPException(status_code=500, detail="Could not create checkout session.")

@app.post("/billing/stripe-webhook", tags=["Billing"])
async def stripe_webhook(request: Request):
    """
    Stripe calls this when subscription events occur.
    Verifies the webhook signature (prevents spoofing), then updates Firestore.

    Events handled:
      customer.subscription.created / invoice.paid  → active = True
      customer.subscription.deleted                 → active = False
    """
    if not stripe_available:
        raise HTTPException(status_code=503, detail="Stripe not configured.")

    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe_lib.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe_lib.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature mismatch.")
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    obj = event["data"]["object"]
    uid = (obj.get("metadata") or {}).get("firebase_uid") or obj.get("client_reference_id")

    if not uid:
        logger.warning(f"No firebase_uid in Stripe webhook: {event['type']}")
        return {"status": "skipped"}

    if firestore_db:
        if event["type"] in ("customer.subscription.created", "invoice.paid"):
            period_end = None
            if obj.get("current_period_end"):
                period_end = datetime.fromtimestamp(
                    obj["current_period_end"], tz=timezone.utc
                ).isoformat()

            firestore_db.collection("subscribers").document(uid).set(
                {
                    "active":           True,
                    "stripe_status":    obj.get("status", "active"),
                    "period_end":       period_end,
                    "stripe_customer":  obj.get("customer"),
                    "updated_at":       datetime.now(timezone.utc).isoformat(),
                },
                merge=True,
            )
            logger.info(f"Subscriber {uid} activated via Stripe.")

        elif event["type"] == "customer.subscription.deleted":
            firestore_db.collection("subscribers").document(uid).set(
                {
                    "active":        False,
                    "stripe_status": "canceled",
                    "updated_at":    datetime.now(timezone.utc).isoformat(),
                },
                merge=True,
            )
            logger.info(f"Subscriber {uid} deactivated — subscription canceled.")

    return {"status": "processed", "event": event["type"]}

# ─── Weather ──────────────────────────────────────────────────────────────────

@app.post("/check-risk", response_model=WeatherReport, tags=["Weather"])
async def check_weather_risk(
    coords:        Coordinates,
    background:    BackgroundTasks,
    authorization: Optional[str] = Header(None),
):
    """
    Primary endpoint for the FlutterFlow mobile app.
    Subscriber auth required.  Sends GPS coords → full WeatherReport.
    """
    token = await verify_firebase_token(authorization)
    uid   = token.get("uid")

    if not await require_active_subscription(uid):
        raise HTTPException(
            status_code=402,
            detail="Active subscription required. Subscribe for $7/mo at thorshammer.app",
        )

    ctx = assess_location(coords.latitude, coords.longitude)
    if not ctx:
        raise HTTPException(status_code=503, detail="Weather service unavailable.")

    report = _build_report(coords.latitude, coords.longitude, ctx)
    background.add_task(_persist_weather_record, report)

    if ctx["drone_recommended"]:
        background.add_task(
            drone.dispatch_recon,
            coords.latitude, coords.longitude,
            ctx["fire_level"], ctx["lightning_nearby"],
        )

    return report

@app.get("/base-station", response_model=WeatherReport, tags=["Weather"])
async def get_base_station_status(authorization: Optional[str] = Header(None)):
    """
    Current conditions at Taylor Rd / Venable Mountain base station.
    Used for the always-visible home screen weather tile in FlutterFlow.
    """
    token = await verify_firebase_token(authorization)
    if not await require_active_subscription(token.get("uid")):
        raise HTTPException(status_code=402, detail="Subscription required.")

    ctx = assess_location(BASE_STATION_LAT, BASE_STATION_LON)
    if not ctx:
        raise HTTPException(status_code=503, detail="Weather service unavailable.")
    return _build_report(BASE_STATION_LAT, BASE_STATION_LON, ctx)

@app.get("/lightning-strikes", tags=["Weather"])
async def get_lightning_strikes(
    lat:           float = BASE_STATION_LAT,
    lon:           float = BASE_STATION_LON,
    radius_km:     float = 50,
    authorization: Optional[str] = Header(None),
):
    """Lightning strikes within radius_km. Requires Weatherbit Pro+ plan."""
    token = await verify_firebase_token(authorization)
    if not await require_active_subscription(token.get("uid")):
        raise HTTPException(status_code=402, detail="Subscription required.")

    strikes = fetch_lightning_strikes(lat, lon, radius_km)
    return {
        "location":     {"lat": lat, "lon": lon},
        "radius_km":    radius_km,
        "strike_count": len(strikes),
        "strikes":      strikes,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }

@app.get("/weather/history", tags=["Weather"])
async def get_weather_history(
    limit:         int = 50,
    authorization: Optional[str] = Header(None),
):
    """Last N weather risk records from Firestore."""
    token = await verify_firebase_token(authorization)
    if not await require_active_subscription(token.get("uid")):
        raise HTTPException(status_code=402, detail="Subscription required.")

    if not firestore_db:
        return {"total_records": 0, "records": []}

    docs = (
        firestore_db.collection("weather_records")
        .order_by("timestamp", direction="DESCENDING")
        .limit(limit)
        .stream()
    )
    return {"records": [d.to_dict() for d in docs]}

# ─── Drone ────────────────────────────────────────────────────────────────────

@app.post("/drone/dispatch", tags=["Drone"])
async def dispatch_drone(
    cmd:           DroneCommand,
    authorization: Optional[str] = Header(None),
):
    """Manual operator dispatch from the FlutterFlow operator screen."""
    await verify_firebase_token(authorization)
    target_lat = cmd.target_lat or BASE_STATION_LAT
    target_lon = cmd.target_lon or BASE_STATION_LON

    mission = drone.dispatch_recon(
        target_lat, target_lon,
        fire_risk_level="MANUAL_DISPATCH",
        lightning_nearby=False,
        altitude_m=cmd.altitude_m,
        capture_interval_sec=cmd.capture_interval_sec,
        operator_override=True,
    )
    if cmd.notes and mission.get("mission_id") and firestore_db:
        firestore_db.collection("drone_missions").document(
            mission["mission_id"]
        ).update({"operator_notes": cmd.notes})

    return {"status": "queued", "mission": mission}

@app.get("/drone/missions/pending", tags=["Drone"])
async def get_pending_missions(authorization: Optional[str] = Header(None)):
    """Polled by the FlutterFlow operator tablet (Timer widget, 30 s interval)."""
    await verify_firebase_token(authorization)
    pending = drone.get_pending()
    return {"pending_count": len(pending), "missions": pending}

@app.post("/drone/missions/{mission_id}/acknowledge", tags=["Drone"])
async def acknowledge_mission(
    mission_id:    str,
    authorization: Optional[str] = Header(None),
):
    """Operator app calls this when it picks up and starts executing a mission."""
    await verify_firebase_token(authorization)
    if not drone.acknowledge(mission_id):
        raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found.")
    return {"status": "acknowledged", "mission_id": mission_id}

@app.post("/drone/missions/{mission_id}/complete", tags=["Drone"])
async def complete_mission(
    mission_id:    str,
    authorization: Optional[str] = Header(None),
):
    await verify_firebase_token(authorization)
    if not drone.complete(mission_id):
        raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found.")
    return {"status": "completed", "mission_id": mission_id}

@app.post("/drone/missions/{mission_id}/abort", tags=["Drone"])
async def abort_mission(
    mission_id:    str,
    reason:        str = "operator abort",
    authorization: Optional[str] = Header(None),
):
    """Emergency stop — marks mission ABORTED and records the reason."""
    await verify_firebase_token(authorization)
    if not drone.abort(mission_id, reason):
        raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found.")
    return {"status": "aborted", "mission_id": mission_id, "reason": reason}

@app.get("/drone/mission-log", tags=["Drone"])
async def get_mission_log(
    limit:         int = 50,
    authorization: Optional[str] = Header(None),
):
    """Full mission history from Firestore — audit trail and ops dashboard."""
    await verify_firebase_token(authorization)
    return {"missions": drone.get_log(limit)}

@app.post("/drone/upload-image", tags=["Drone"])
async def upload_drone_image(
    file:          UploadFile = File(...),
    mission_id:    str = "unknown",
    authorization: Optional[str] = Header(None),
):
    """
    Receives recon images from the Mavic Pro 4 mid-mission.

    TODO — production upgrade path (in order):
      1. Replace local disk write with Google Cloud Storage:
           from google.cloud import storage
           bucket = storage.Client().bucket("thorshammer-drone-imagery")
           bucket.blob(f"{mission_id}/{file.filename}").upload_from_string(contents)
      2. Pass image to smoke/fire detection:
           Option A: Google Vision API (no training required)
           Option B: Custom TFLite model trained on wildfire smoke frames
      3. If smoke_detected == True: call broadcast_alert_to_subscribers() immediately.
    """
    await verify_firebase_token(authorization)
    contents = await file.read()

    # Local fallback for development / field testing
    save_dir  = os.path.join("drone_imagery", mission_id)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, file.filename or "frame.jpg")
    with open(save_path, "wb") as f:
        f.write(contents)

    logger.info(f"🖼  Image saved: {save_path}  ({len(contents):,} bytes)")

    smoke_detected = None  # populated once vision model is integrated

    if firestore_db:
        # ArrayUnion appends to a Firestore array field without reading it first
        firestore_db.collection("drone_missions").document(mission_id).update({
            "images": firestore.ArrayUnion([{   # type: ignore[attr-defined]
                "filename":       file.filename,
                "saved_to":       save_path,
                "size_bytes":     len(contents),
                "uploaded_at":    datetime.now(timezone.utc).isoformat(),
                "smoke_detected": smoke_detected,
            }])
        })

    return {
        "status":         "received",
        "filename":       file.filename,
        "mission_id":     mission_id,
        "size_bytes":     len(contents),
        "smoke_detected": smoke_detected,
    }

# ─── Notifications ────────────────────────────────────────────────────────────

@app.post("/notify", tags=["Notifications"])
async def send_notification(
    payload:       AlertPayload,
    authorization: Optional[str] = Header(None),
):
    """Send a push to a single FCM token. Operator / internal use only."""
    await verify_firebase_token(authorization)
    ok = send_push_to_token(
        payload.device_token, payload.title, payload.body, payload.data
    )
    if not ok:
        raise HTTPException(status_code=503, detail="Notification failed — check Firebase.")
    return {"status": "sent"}

# ═════════════════════════════════════════════════════════════════════════════
# LOCAL DEV ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
#  Local (no Docker):   python thorshammer_v2_01.py
#  Docker Compose:      docker-compose up --build
#  Production (GCP):    uvicorn thorshammer_v2_01:app --host 0.0.0.0 --port 8000 --workers 2
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
