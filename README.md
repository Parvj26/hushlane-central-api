# HushLane Central API

This is the central monitoring and version management API for HushLane. It should be deployed separately on a server (e.g., api.hushlane.app).

## Features

- **Version Management**: Distribute latest version info to all customer instances
- **Instance Registration**: Track all customer deployments
- **Master Admin Dashboard**: Monitor health, versions, and statistics
- **Update History**: Log version changes across all customers

## Quick Start

### Local Development

```bash
cd central-api

# Install dependencies
pip install -r requirements.txt

# Set admin credentials (optional, defaults are admin/changeme123)
export MASTER_ADMIN_USERNAME=admin
export MASTER_ADMIN_PASSWORD=your-secure-password

# Run the server
python main.py
```

Access:
- API: http://localhost:8001
- Admin Dashboard: http://localhost:8001/admin (use credentials above)

### Production Deployment

#### Option 1: Docker (Recommended)

```bash
# Build image
docker build -t hushlane-central-api .

# Run container
docker run -d \
  -p 8001:8001 \
  -e MASTER_ADMIN_USERNAME=admin \
  -e MASTER_ADMIN_PASSWORD=your-secure-password \
  -v $(pwd)/data:/app/data \
  --name hushlane-central \
  hushlane-central-api
```

#### Option 2: Direct Deployment

```bash
# Install dependencies
pip install -r requirements.txt

# Run with gunicorn (production server)
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8001
```

#### Option 3: Serverless (Vercel/Railway/etc.)

Deploy the `main.py` file to any serverless platform that supports Python FastAPI.

## API Endpoints

### GET /latest-version

Returns the latest available version for customer instances.

**Response:**
```json
{
  "version": "1.0.0",
  "released": "2026-01-18",
  "changelog_url": "https://hushlane.app/changelog",
  "critical": false
}
```

### POST /instances/register

Receives heartbeat from customer instances.

**Request Body:**
```json
{
  "customer_id": "acme",
  "version": "1.0.0",
  "url": "https://acme.hushlane.app",
  "health": "healthy",
  "timestamp": "2026-01-18T12:00:00Z",
  "total_users": 10,
  "total_messages": 500
}
```

### GET /admin

Master admin dashboard (requires HTTP Basic Auth).

**Credentials:**
- Username: Set via `MASTER_ADMIN_USERNAME` env var (default: admin)
- Password: Set via `MASTER_ADMIN_PASSWORD` env var (default: changeme123)

## Configuration

### Environment Variables

```bash
# Admin credentials for /admin dashboard
MASTER_ADMIN_USERNAME=admin
MASTER_ADMIN_PASSWORD=your-secure-password
```

### Updating Latest Version

When releasing a new version, update the `LATEST_VERSION` constant in `main.py`:

```python
LATEST_VERSION = "1.1.0"  # Update this
```

Then restart the central API server. All customer instances will be notified within 24 hours (or immediately if they check manually).

## Database

Uses SQLite for simplicity. Database file is stored at `instances.db`.

### Schema

**customer_instances**
- customer_id (PK)
- version
- url
- health_status
- last_heartbeat
- first_seen
- total_users
- total_messages

**version_history**
- id (PK)
- customer_id (FK)
- old_version
- new_version
- updated_at

## Security

1. **Change default admin password** in production!
2. Use HTTPS (configure reverse proxy like Nginx or use Cloudflare)
3. Keep the central API private - only expose necessary endpoints
4. Regularly backup the SQLite database

## Monitoring

### Health Check

```bash
curl http://localhost:8001/health
```

### View Logs

```bash
# Docker
docker logs hushlane-central

# Direct deployment
# Check your process manager logs
```

## Backup

Backup the SQLite database regularly:

```bash
# Docker
docker exec hushlane-central sqlite3 /app/instances.db ".backup /app/data/backup.db"

# Direct
sqlite3 instances.db ".backup backup.db"
```

## Scaling

For large deployments (100+ customers):
1. Migrate from SQLite to PostgreSQL
2. Add Redis for caching
3. Use load balancer for high availability
4. Add monitoring (Prometheus, Grafana)

## Support

For issues or questions, contact the HushLane development team.
