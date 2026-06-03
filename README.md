# Web Scraper Platform

A **production-ready FastAPI backend** for scraping company, school, college, and institute websites. Extracts structured information such as organization name, address, phone numbers, emails, social media links, and descriptions.

---

## Features

- **Single URL scraping** – Submit one URL and get structured data back
- **Bulk URL scraping** – Process hundreds of URLs asynchronously
- **File upload** – Upload `.xlsx` or `.csv` files with URLs
- **Automatic Contact Us page discovery** – Finds and scrapes contact pages
- **JavaScript rendering** – Playwright support for JS-heavy sites
- **Async architecture** – FastAPI + asyncio for high throughput
- **Background jobs** – Celery (database-backed) for distributed task processing
- **Export** – Download results as JSON, CSV, or Excel
- **Docker support** – One-command deployment with Docker Compose
- **Rate limiting** – Token-bucket rate limiter
- **User-Agent rotation** – Avoids detection and blocking
- **Configurable crawl depth** – Control how deep to follow internal links
- **Retry logic** – Automatic retries with exponential backoff

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** (recommended)
- Or: **Python 3.12+**, **MySQL 8**

### 1. Clone & Configure

```bash
git clone <your-repo-url>
cd web-scraper-platform
cp .env.example .env
```

### 2. Run with Docker Compose

```bash
docker compose up -d
```

This starts:
- **API** at `http://localhost:8000`
- **Celery Worker** for background scraping
- **MySQL** on port `3306`

### 3. Access the API

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## Manual Setup (Without Docker)

### 1. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browsers

```bash
playwright install --with-deps chromium
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env with your database credentials
```

### 5. Run database migrations

```bash
alembic upgrade head
```

### 6. Start the API server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 7. Start the Celery worker (in a separate terminal)

```bash
celery -A app.worker.tasks.celery_app worker --loglevel=info --concurrency=4
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/scrape/url` | Scrape a single website URL |
| `POST` | `/api/v1/scrape/bulk` | Scrape multiple URLs (async) |
| `POST` | `/api/v1/scrape/upload` | Upload Excel/CSV with URLs |
| `GET` | `/api/v1/scrape/status/{job_id}` | Get job progress |
| `GET` | `/api/v1/scrape/result/{job_id}` | Get scraped results |
| `GET` | `/api/v1/scrape/jobs` | List all jobs (paginated) |
| `GET` | `/api/v1/export/{job_id}` | Export results (JSON/CSV/Excel) |
| `GET` | `/health` | Health check |

### Example: Scrape a Single URL

```bash
curl -X POST http://localhost:8000/api/v1/scrape/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "crawl_depth": 2}'
```

### Example: Scrape Multiple URLs

```bash
curl -X POST http://localhost:8000/api/v1/scrape/bulk \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com", "https://example.org"]}'
```

### Example: Upload a File

```bash
curl -X POST http://localhost:8000/api/v1/scrape/upload \
  -F "file=@urls.xlsx"
```

### Example: Check Job Status

```bash
curl http://localhost:8000/api/v1/scrape/status/550e8400-e29b-41d4-a716-446655440000
```

### Example: Export Results

```bash
curl -O http://localhost:8000/api/v1/export/550e8400-e29b-41d4-a716-446655440000?format=csv
```

---

## Project Structure

```
web-scraper-platform/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Pydantic settings (.env)
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── job.py           # Scraping job
│   │   ├── url.py           # Individual URL status
│   │   ├── result.py        # Scraped data
│   │   ├── log.py           # Execution logs
│   │   └── upload.py        # File upload history
│   ├── schemas/             # Pydantic request/response schemas
│   │   ├── scrape.py
│   │   ├── job.py
│   │   └── export.py
│   ├── routes/
│   │   └── scrape.py        # All API endpoints
│   ├── services/
│   │   ├── scraper.py       # Main scraper engine (httpx + Playwright)
│   │   ├── extractors.py    # Data extraction utilities
│   │   └── normalizer.py    # Data cleaning & normalization
│   ├── utils/
│   │   ├── validators.py    # URL/email/phone validation
│   │   ├── user_agents.py   # UA rotation pool
│   │   └── rate_limiter.py  # Token-bucket rate limiter
│   └── worker/
│       └── tasks.py         # Celery task definitions
├── alembic/                 # Database migrations
├── docker/
│   └── Dockerfile.worker    # Celery worker Dockerfile
├── docker-compose.yml       # Multi-service orchestration
├── Dockerfile               # API Docker image
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
├── .gitignore
└── README.md
```

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_USER` | `root` | MySQL user |
| `DB_PASS` | `root` | MySQL password |
| `DB_NAME` | `web_scraper_db` | MySQL database name |
| `DB_DIALECT` | `mysql` | SQL dialect (`mysql`, `mariadb`) |
| `SCRAPER_TIMEOUT_SECONDS` | `30` | Page fetch timeout |
| `SCRAPER_MAX_RETRIES` | `3` | Max retry attempts per URL |
| `SCRAPER_CRAWL_DEPTH` | `2` | Internal link crawl depth |
| `SCRAPER_CONCURRENT_REQUESTS` | `5` | Max parallel scrapes |
| `SCRAPER_RESPECT_ROBOTS_TXT` | `true` | Honor robots.txt |
| `SCRAPER_USER_AGENT_ROTATION` | `true` | Rotate User-Agent headers |
| `PROXY_ENABLED` | `false` | Enable proxy support |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | API rate limit |

---

## Deployment

### DigitalOcean Droplet (4 GB RAM) – One-Click Deploy

This project comes with a **cloud-init script** that automatically sets up the entire stack on a DigitalOcean droplet.

#### Prerequisites

1. Push this project to a **GitHub repository** (or any Git host)
2. A **DigitalOcean Managed MySQL** database (already provisioned)
3. A **DEEPSEEK_API_KEY** (optional, for AI-powered extraction)

#### Step 1: Update the Git Repo URL

Edit `deploy/user-data.sh` and replace the placeholder:

```bash
GIT_REPO="https://github.com/YOUR_USERNAME/web-scraper-platform.git"
GIT_BRANCH="main"
```

The DB credentials are **already configured** in the script — no changes needed there.

#### Step 2: Create the Droplet

1. Go to [DigitalOcean Cloud](https://cloud.digitalocean.com) → **Droplets** → **Create Droplet**
2. Choose:
   - **Region**: `Bangalore` (BLR1) — same region as your managed DB for lowest latency
   - **Image**: `Ubuntu 24.04 LTS`
   - **Size**: `Basic` → `4 GB RAM / 2 vCPUs`
3. Under **Advanced Options** → **User Data**, paste the **entire contents** of `deploy/user-data.sh`
4. Add your SSH key for access
5. Click **Create Droplet**

#### Step 3: Wait & Verify

The cloud-init script runs on first boot (~5-8 minutes for full setup). Monitor progress:

```bash
# SSH into the droplet
ssh root@<droplet-ip>

# Tail the deployment log
tail -f /var/log/web-scraper-deploy.log

# Check container status
docker compose -f /opt/web-scraper/deploy/docker-compose.prod.yml ps
```

#### Step 4: Access the App

| Service | URL |
|---------|-----|
| **Web UI** | `http://<droplet-ip>/` |
| **Swagger Docs** | `http://<droplet-ip>/docs` |
| **Health Check** | `http://<droplet-ip>/health` |

> **Note**: Add your `DEEPSEEK_API_KEY` to `/opt/web-scraper/.env.production` on the droplet and restart:
> ```bash
> docker compose -f /opt/web-scraper/deploy/docker-compose.prod.yml up -d --force-recreate
> ```

#### Architecture on the Droplet

```
┌─────────────────────────────────────────┐
│  DigitalOcean Droplet (4 GB RAM)        │
│                                          │
│  ┌──────────┐   ┌──────────────────┐   │
│  │  Nginx    │   │                  │   │
│  │  (later)  │   │  Celery Worker   │   │
│  │          │   │  (2 concurrency)  │   │
│  │  FastAPI │   │  + Playwright     │   │
│  │  (2 uvicorn  │  + Chromium       │   │
│  │   workers)   │                  │   │
│  └──────────┘   └──────────────────┘   │
│         │               │               │
│         2 GB swap file                  │
└─────────┼───────────────┼───────────────┘
          │               │
    ┌─────┴───────────────┴─────┐
    │  DO Managed MySQL (BLR1)  │
    │  db-mysql-blr1-66672...   │
    └───────────────────────────┘
```

#### Manual Deployment (Without Cloud-Init)

If you prefer to deploy manually on an existing droplet:

```bash
# 1. Clone the repo
git clone <your-repo-url> /opt/web-scraper
cd /opt/web-scraper

# 2. Copy and edit the env file
cp .env.production .env.production.local
# Edit with your actual values

# 3. Start services
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

### Production Considerations

1. **Set `DEBUG=false`** — already done in `.env.production`
2. **Add a reverse proxy** — Run Nginx or Caddy in front for SSL termination
3. **Firewall** — Use DigitalOcean Cloud Firewall to allow only ports 80 & 443
4. **Set up a domain** — Point DNS to the droplet IP and use Let's Encrypt
5. **Scale workers** — Add more Celery worker containers for higher throughput
6. **Monitoring** — Add Prometheus + Grafana, or Sentry for error tracking

---

## License

MIT
