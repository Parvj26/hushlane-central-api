"""
Central API for HushLane - Manages customer instances and version distribution
Deploy this on a separate server (e.g., api.hushlane.app)
"""
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from datetime import datetime
import secrets
import aiosqlite
import os

app = FastAPI(title="HushLane Central API")
templates = Jinja2Templates(directory="templates")
security = HTTPBasic()

# Configuration
LATEST_VERSION = "1.0.0"  # Update this when releasing new versions
DATABASE_PATH = "instances.db"
MASTER_ADMIN_USERNAME = os.getenv("MASTER_ADMIN_USERNAME", "admin")
MASTER_ADMIN_PASSWORD = os.getenv("MASTER_ADMIN_PASSWORD", "changeme123")  # Change in production!
LICENSE_SECRET = os.getenv("LICENSE_SECRET", "change-this-secret-key-in-production")


class InstanceRegistration(BaseModel):
    customer_id: str
    version: str
    url: str
    health: str
    timestamp: str
    total_users: int = 0
    total_messages: int = 0


class LicenseValidation(BaseModel):
    license_key: str
    customer_id: str
    app_version: str
    timestamp: str


async def init_db():
    """Initialize database schema."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS customer_instances (
                customer_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                url TEXT NOT NULL,
                health_status TEXT DEFAULT 'healthy',
                last_heartbeat TIMESTAMP,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_users INTEGER DEFAULT 0,
                total_messages INTEGER DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS version_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT,
                old_version TEXT,
                new_version TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES customer_instances(customer_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                license_key TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL,
                plan TEXT DEFAULT 'standard',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                last_validated TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES customer_instances(customer_id)
            )
        """)

        await db.commit()


@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    await init_db()


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "HushLane Central API",
        "version": "1.0.0",
        "endpoints": {
            "version": "/latest-version",
            "register": "/instances/register",
            "admin": "/admin"
        }
    }


@app.get("/latest-version")
async def get_latest_version():
    """Return latest available version for customer instances."""
    return {
        "version": LATEST_VERSION,
        "released": "2026-01-18",
        "changelog_url": "https://hushlane.app/changelog",
        "critical": False  # Set to True for critical security updates
    }


@app.post("/instances/register")
async def register_instance(registration: InstanceRegistration):
    """Register or update a customer instance."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Check if instance exists
            cursor = await db.execute(
                "SELECT version FROM customer_instances WHERE customer_id = ?",
                (registration.customer_id,)
            )
            row = await cursor.fetchone()

            if row:
                old_version = row[0]
                # Update existing instance
                await db.execute("""
                    UPDATE customer_instances
                    SET version = ?, url = ?, health_status = ?,
                        last_heartbeat = ?, total_users = ?, total_messages = ?
                    WHERE customer_id = ?
                """, (
                    registration.version,
                    registration.url,
                    registration.health,
                    registration.timestamp,
                    registration.total_users,
                    registration.total_messages,
                    registration.customer_id
                ))

                # Log version change if version updated
                if old_version != registration.version:
                    await db.execute("""
                        INSERT INTO version_history (customer_id, old_version, new_version)
                        VALUES (?, ?, ?)
                    """, (registration.customer_id, old_version, registration.version))
            else:
                # Insert new instance
                await db.execute("""
                    INSERT INTO customer_instances
                    (customer_id, version, url, health_status, last_heartbeat, total_users, total_messages)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    registration.customer_id,
                    registration.version,
                    registration.url,
                    registration.health,
                    registration.timestamp,
                    registration.total_users,
                    registration.total_messages
                ))

            await db.commit()

        return {"status": "success", "message": "Instance registered"}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


def verify_master_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify master admin credentials."""
    correct_username = secrets.compare_digest(credentials.username, MASTER_ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, MASTER_ADMIN_PASSWORD)

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/admin", response_class=HTMLResponse)
async def master_admin_dashboard(
    request: Request,
    username: str = Depends(verify_master_admin)
):
    """Master admin dashboard showing all customer instances."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Fetch all customer instances
        cursor = await db.execute("""
            SELECT * FROM customer_instances
            ORDER BY last_heartbeat DESC
        """)
        instances_rows = await cursor.fetchall()
        instances = [dict(row) for row in instances_rows]

        # Calculate statistics
        total_customers = len(instances)
        healthy_count = sum(1 for i in instances if i['health_status'] == 'healthy')
        outdated_count = sum(1 for i in instances if i['version'] != LATEST_VERSION)

        # Recent updates
        cursor = await db.execute("""
            SELECT * FROM version_history
            ORDER BY updated_at DESC
            LIMIT 10
        """)
        updates_rows = await cursor.fetchall()
        recent_updates = [dict(row) for row in updates_rows]

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "instances": instances,
        "total_customers": total_customers,
        "healthy_count": healthy_count,
        "outdated_count": outdated_count,
        "recent_updates": recent_updates,
        "latest_version": LATEST_VERSION
    })


@app.post("/license/validate")
async def validate_license(validation: LicenseValidation):
    """Validate a customer license key."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Look up license
            cursor = await db.execute("""
                SELECT * FROM licenses
                WHERE license_key = ?
            """, (validation.license_key,))
            license_row = await cursor.fetchone()

            if not license_row:
                return JSONResponse(
                    status_code=401,
                    content={
                        "valid": False,
                        "error": "INVALID_LICENSE",
                        "message": "License key not found"
                    }
                )

            license_data = dict(license_row)

            # Check if license is active
            if license_data['status'] != 'active':
                return JSONResponse(
                    status_code=401,
                    content={
                        "valid": False,
                        "error": "LICENSE_INACTIVE",
                        "message": f"License status: {license_data['status']}"
                    }
                )

            # Check if expired
            if license_data['expires_at']:
                expires_at = datetime.fromisoformat(license_data['expires_at'])
                if datetime.now() > expires_at:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "valid": False,
                            "error": "LICENSE_EXPIRED",
                            "message": f"License expired on {expires_at.strftime('%Y-%m-%d')}"
                        }
                    )

            # Check customer ID matches
            if license_data['customer_id'] != validation.customer_id:
                return JSONResponse(
                    status_code=401,
                    content={
                        "valid": False,
                        "error": "CUSTOMER_MISMATCH",
                        "message": "License key does not match customer ID"
                    }
                )

            # Update last validated timestamp
            await db.execute("""
                UPDATE licenses
                SET last_validated = ?
                WHERE license_key = ?
            """, (datetime.now().isoformat(), validation.license_key))
            await db.commit()

            # Return success
            return {
                "valid": True,
                "customer_name": license_data['customer_name'],
                "plan": license_data['plan'],
                "expires_at": license_data['expires_at'],
                "message": "License valid"
            }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "valid": False,
                "error": "VALIDATION_ERROR",
                "message": str(e)
            }
        )


@app.get("/health")
async def health():
    """Health check for central API."""
    return {"status": "healthy", "service": "central-api"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
