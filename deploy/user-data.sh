#!/bin/bash
# ============================================================================
# DigitalOcean Droplet Cloud-Init – Web Scraper Platform (4 GB RAM)
# ============================================================================
#
# Paste this entire script into the "User Data" field when creating a
# DigitalOcean droplet running Ubuntu 24.04 LTS.
#
# What it does:
#   1. Updates system packages & sets up 2 GB swap (critical for Playwright)
#   2. Installs Docker + Docker Compose plugin
#   3. Clones the project repository
#   4. Creates .env.production with DB credentials
#   5. Builds and starts all services via docker compose
# ============================================================================
set -e

# ── Config ─────────────────────────────────────────────────────────────────
# >>> CHANGE THIS to your actual Git repository URL <<<
GIT_REPO="https://github.com/YOUR_USERNAME/web-scraper-platform.git"
GIT_BRANCH="main"

APP_DIR="/opt/web-scraper"

# DO Managed MySQL credentials — FILL THESE IN before creating the droplet
DB_HOST="YOUR_DB_HOST"
DB_USER="YOUR_DB_USER"
DB_PASS="YOUR_DB_PASS"
DB_NAME="web_scraper_db"
DB_DIALECT="mysql"
DB_PORT="25060"

LOG_FILE="/var/log/web-scraper-deploy.log"

# ── Logging ────────────────────────────────────────────────────────────────
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== Web Scraper Platform Deployment Started: $(date) ==="

# ── 1. System Update & Swap ────────────────────────────────────────────────
echo "[1/6] Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq

# Create 2 GB swap file (Playwright + Chromium need extra memory on 4 GB droplet)
if [ ! -f /swapfile ]; then
    echo "Creating 2 GB swap file..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Swap created and enabled."
else
    echo "Swap already exists, skipping."
fi

# ── 2. Install Docker ──────────────────────────────────────────────────────
echo "[2/6] Installing Docker..."
apt-get install -y -qq ca-certificates curl

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker
echo "Docker installed: $(docker --version)"

# ── 3. Clone Repository ────────────────────────────────────────────────────
echo "[3/6] Cloning repository from $GIT_REPO (branch: $GIT_BRANCH)..."
if [ -d "$APP_DIR" ]; then
    echo "Directory $APP_DIR already exists, pulling latest changes..."
    cd "$APP_DIR"
    git fetch origin
    git reset --hard "origin/$GIT_BRANCH"
else
    git clone --branch "$GIT_BRANCH" --depth 1 "$GIT_REPO" "$APP_DIR"
    cd "$APP_DIR"
fi

# ── 4. Create .env.production ──────────────────────────────────────────────
echo "[4/6] Creating .env.production..."
cat > "$APP_DIR/.env.production" << ENVEOF
# ── Application ──────────────────────────────────────────────
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO
DEEPSEEK_API_KEY=

# ── Database (DigitalOcean Managed MySQL) ────────────────────
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_USER=$DB_USER
DB_PASS=$DB_PASS
DB_NAME=$DB_NAME
DB_DIALECT=$DB_DIALECT
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=5
DATABASE_ECHO=false

# ── Celery (database-backed broker & result backend) ─────────
# These are auto-derived from DB_* vars by config.py — no need to set separately

# ── Scraper ──────────────────────────────────────────────────
SCRAPER_CONCURRENT_REQUESTS=3
SCRAPER_TIMEOUT_SECONDS=30
SCRAPER_HEADLESS_BROWSER=true
SCRAPER_USER_AGENT_ROTATION=true
SCRAPER_MAX_URLS_PER_JOB=200
ENVEOF

chmod 600 "$APP_DIR/.env.production"
echo ".env.production created."

# ── 5. Create Directories ──────────────────────────────────────────────────
echo "[5/6] Creating data directories..."
mkdir -p "$APP_DIR/uploads" "$APP_DIR/exports"
chown -R 1000:1000 "$APP_DIR/uploads" "$APP_DIR/exports" 2>/dev/null || true

# ── 6. Build & Start Services ──────────────────────────────────────────────
echo "[6/6] Building and starting Docker containers..."
cd "$APP_DIR"
docker compose -f deploy/docker-compose.prod.yml up -d --build --remove-orphans

# ── Verify ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Deployment Complete: $(date) ==="
echo ""
echo "Containers running:"
docker compose -f deploy/docker-compose.prod.yml ps
echo ""
echo "API health check (may take ~30s for containers to start):"
sleep 10
curl -sf http://localhost:8000/health 2>/dev/null && echo "" || echo "(still starting — check again in a minute)"
echo ""
echo "Check logs:  docker compose -f $APP_DIR/deploy/docker-compose.prod.yml logs -f"
echo "Log file:    $LOG_FILE"
echo "API docs:    http://<droplet-ip>/docs"
echo "Web UI:      http://<droplet-ip>/"
