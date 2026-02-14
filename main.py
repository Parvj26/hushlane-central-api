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
from typing import Optional
import secrets
import base64
import aiosqlite
import httpx
import os

app = FastAPI(title="HushLane Central API")
templates = Jinja2Templates(directory="templates")
security = HTTPBasic()

# Configuration
LATEST_VERSION = "1.0.5"  # Update this when releasing new versions

# Database path - use persistent storage on Render
# Render mounts persistent disk at /var/data (separate from code)
# Fallback to ./data for local development
DATA_DIR = os.getenv("DATA_DIR", "/var/data" if os.path.exists("/var/data") else "data")
DATABASE_PATH = os.path.join(DATA_DIR, "instances.db")

MASTER_ADMIN_USERNAME = os.getenv("MASTER_ADMIN_USERNAME", "admin")
MASTER_ADMIN_PASSWORD = os.getenv("MASTER_ADMIN_PASSWORD", "changeme123")  # Change in production!
LICENSE_SECRET = os.getenv("LICENSE_SECRET", "change-this-secret-key-in-production")

# Cloudflare Configuration
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_ZONE_ID = os.getenv("CLOUDFLARE_ZONE_ID", "")
CLOUDFLARE_DOMAIN = os.getenv("CLOUDFLARE_DOMAIN", "hushlane.app")


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


class LicenseCreate(BaseModel):
    customer_id: str
    customer_name: str
    plan: str = "standard"
    months: Optional[int] = 12  # None/null for lifetime license


class TunnelCreate(BaseModel):
    customer_id: str


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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tunnels (
                tunnel_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL UNIQUE,
                tunnel_name TEXT NOT NULL,
                tunnel_token TEXT NOT NULL,
                hostname TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT
            )
        """)

        await db.commit()


@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    # Create data directory if it doesn't exist
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"✓ Data directory ready: {os.path.abspath(DATA_DIR)}")

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
        "released": "2026-01-19",
        "changelog_url": "https://hushlane.app/changelog",
        "critical": True  # CRITICAL: Fixes media upload bug
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


@app.delete("/instances/{customer_id}")
async def delete_instance(
    customer_id: str,
    username: str = Depends(verify_master_admin)
):
    """Delete a customer instance from the database. Requires admin authentication."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Check if instance exists
            cursor = await db.execute(
                "SELECT customer_id FROM customer_instances WHERE customer_id = ?",
                (customer_id,)
            )
            row = await cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail=f"Instance '{customer_id}' not found")

            # Delete from all tables
            await db.execute("DELETE FROM version_history WHERE customer_id = ?", (customer_id,))
            await db.execute("DELETE FROM licenses WHERE customer_id = ?", (customer_id,))
            await db.execute("DELETE FROM customer_instances WHERE customer_id = ?", (customer_id,))
            await db.commit()

            return {
                "success": True,
                "message": f"Instance '{customer_id}' deleted successfully",
                "deleted_by": username
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting instance: {str(e)}")


@app.post("/licenses/create")
async def create_license(
    license_data: LicenseCreate,
    username: str = Depends(verify_master_admin)
):
    """Create a new license for a customer. Requires admin authentication."""
    try:
        # Generate license key in format: HL-XXXX-XXXX-XXXX-XXXX
        parts = [secrets.token_hex(4).upper() for _ in range(4)]
        license_key = f"HL-{'-'.join(parts)}"

        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Check if customer already has a license
            cursor = await db.execute(
                "SELECT license_key FROM licenses WHERE customer_id = ?",
                (license_data.customer_id,)
            )
            existing = await cursor.fetchone()

            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer '{license_data.customer_id}' already has a license: {existing[0]}"
                )

            # Calculate expiration
            from datetime import timedelta
            expires_at = None
            if license_data.months:
                expires_at = (datetime.now() + timedelta(days=license_data.months * 30)).isoformat()

            # Insert license
            await db.execute("""
                INSERT INTO licenses (license_key, customer_id, customer_name, plan, status, expires_at)
                VALUES (?, ?, ?, ?, 'active', ?)
            """, (license_key, license_data.customer_id, license_data.customer_name, license_data.plan, expires_at))

            await db.commit()

            return {
                "success": True,
                "license_key": license_key,
                "customer_id": license_data.customer_id,
                "customer_name": license_data.customer_name,
                "plan": license_data.plan,
                "status": "active",
                "expires_at": expires_at,
                "created_by": username
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating license: {str(e)}")


@app.post("/licenses/{customer_id}/revoke")
async def revoke_license(
    customer_id: str,
    username: str = Depends(verify_master_admin)
):
    """Revoke a customer license. Requires admin authentication."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Check if license exists
            cursor = await db.execute(
                "SELECT license_key, status FROM licenses WHERE customer_id = ?",
                (customer_id,)
            )
            row = await cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail=f"License for '{customer_id}' not found")

            # Update status to revoked
            await db.execute(
                "UPDATE licenses SET status = 'revoked' WHERE customer_id = ?",
                (customer_id,)
            )
            await db.commit()

            return {
                "success": True,
                "message": f"License for '{customer_id}' revoked successfully",
                "revoked_by": username
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error revoking license: {str(e)}")


@app.delete("/licenses/{customer_id}")
async def delete_license(
    customer_id: str,
    username: str = Depends(verify_master_admin)
):
    """Permanently delete a customer license. Requires admin authentication."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Check if license exists
            cursor = await db.execute(
                "SELECT license_key FROM licenses WHERE customer_id = ?",
                (customer_id,)
            )
            row = await cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail=f"License for '{customer_id}' not found")

            # Delete the license
            await db.execute("DELETE FROM licenses WHERE customer_id = ?", (customer_id,))
            await db.commit()

            return {
                "success": True,
                "message": f"License for '{customer_id}' deleted permanently",
                "deleted_by": username
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting license: {str(e)}")


@app.post("/tunnels/create")
async def create_cloudflare_tunnel(
    tunnel_data: TunnelCreate,
    username: str = Depends(verify_master_admin)
):
    """Create a Cloudflare tunnel for a customer. Requires admin authentication."""

    # Check if Cloudflare is configured
    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ACCOUNT_ID:
        raise HTTPException(
            status_code=500,
            detail="Cloudflare API not configured. Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID environment variables."
        )

    customer_id = tunnel_data.customer_id
    tunnel_name = f"{customer_id}-hushlane"
    subdomain = customer_id

    try:
        # Check if tunnel already exists in database
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT tunnel_id, tunnel_token, hostname FROM tunnels WHERE customer_id = ?",
                (customer_id,)
            )
            existing = await cursor.fetchone()

            if existing:
                return {
                    "success": True,
                    "tunnel_id": existing[0],
                    "tunnel_name": tunnel_name,
                    "tunnel_token": existing[1],
                    "customer_id": customer_id,
                    "hostname": existing[2],
                    "message": "Tunnel already exists (retrieved from database)",
                    "created_by": username
                }

        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }

            # 1. Create the tunnel
            # Generate 32 random bytes and encode as base64 (Cloudflare requirement)
            tunnel_secret = base64.b64encode(secrets.token_bytes(32)).decode('utf-8')

            create_tunnel_response = await client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel",
                headers=headers,
                json={
                    "name": tunnel_name,
                    "tunnel_secret": tunnel_secret
                }
            )

            if create_tunnel_response.status_code != 200:
                error_data = create_tunnel_response.json()
                errors = error_data.get('errors', [])

                # Check if tunnel already exists in Cloudflare (error code 1009 or contains "already exists")
                error_msg = str(errors).lower()
                if any(err.get('code') == 1009 for err in errors if isinstance(err, dict)) or 'already exists' in error_msg or 'duplicate' in error_msg:
                    # Tunnel exists in Cloudflare, try to find it
                    print(f"Tunnel '{tunnel_name}' already exists in Cloudflare, fetching details...")

                    # List all tunnels to find the existing one
                    list_response = await client.get(
                        f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel",
                        headers=headers
                    )

                    if list_response.status_code == 200:
                        tunnels_data = list_response.json()
                        tunnels = tunnels_data.get('result', [])

                        # Find tunnel by name
                        existing_tunnel = next((t for t in tunnels if t.get('name') == tunnel_name), None)

                        if existing_tunnel:
                            tunnel_id = existing_tunnel['id']
                            hostname = f"{subdomain}.{CLOUDFLARE_DOMAIN}"

                            # Token is not available from API - can't retrieve it after creation
                            tunnel_token = "TOKEN_UNAVAILABLE_CHECK_CLOUDFLARE"

                            # Store in database with what we have
                            async with aiosqlite.connect(DATABASE_PATH) as db:
                                await db.execute("""
                                    INSERT INTO tunnels (tunnel_id, customer_id, tunnel_name, tunnel_token, hostname, created_by)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """, (tunnel_id, customer_id, tunnel_name, tunnel_token, hostname, username))
                                await db.commit()

                            return {
                                "success": True,
                                "tunnel_id": tunnel_id,
                                "tunnel_name": tunnel_name,
                                "tunnel_token": tunnel_token,
                                "customer_id": customer_id,
                                "hostname": hostname,
                                "message": "Tunnel already exists in Cloudflare. Token unavailable - check Cloudflare dashboard or recreate tunnel.",
                                "created_by": username
                            }

                # Other error - raise it
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create tunnel: {errors}"
                )

            tunnel_data_response = create_tunnel_response.json()
            tunnel_id = tunnel_data_response["result"]["id"]
            tunnel_token = tunnel_data_response["result"]["token"]

            # 2. Configure tunnel hostname (if ZONE_ID is provided)
            if CLOUDFLARE_ZONE_ID:
                hostname = f"{subdomain}.{CLOUDFLARE_DOMAIN}"

                config_response = await client.put(
                    f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/{tunnel_id}/configurations",
                    headers=headers,
                    json={
                        "config": {
                            "ingress": [
                                {
                                    "hostname": hostname,
                                    "service": "http://hushlane_app:8000"
                                },
                                {
                                    "service": "http_status:404"
                                }
                            ]
                        }
                    }
                )

                if config_response.status_code != 200:
                    # Tunnel created but config failed - still return token
                    print(f"Warning: Tunnel created but hostname config failed: {config_response.text}")

                # 3. Create DNS record
                dns_response = await client.post(
                    f"https://api.cloudflare.com/client/v4/zones/{CLOUDFLARE_ZONE_ID}/dns_records",
                    headers=headers,
                    json={
                        "type": "CNAME",
                        "name": subdomain,
                        "content": f"{tunnel_id}.cfargotunnel.com",
                        "proxied": True
                    }
                )

                if dns_response.status_code not in [200, 201]:
                    dns_data = dns_response.json()
                    # Check if it's a duplicate record error (code 81057)
                    errors = dns_data.get("errors", [])
                    if errors and errors[0].get("code") == 81057:
                        print(f"DNS record already exists for {hostname}")
                    else:
                        print(f"Warning: Tunnel created but DNS failed: {dns_data.get('errors', 'Unknown error')}")

            # Store tunnel in database
            hostname = f"{subdomain}.{CLOUDFLARE_DOMAIN}"
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("""
                    INSERT INTO tunnels (tunnel_id, customer_id, tunnel_name, tunnel_token, hostname, created_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (tunnel_id, customer_id, tunnel_name, tunnel_token, hostname, username))
                await db.commit()

            return {
                "success": True,
                "tunnel_id": tunnel_id,
                "tunnel_name": tunnel_name,
                "tunnel_token": tunnel_token,
                "customer_id": customer_id,
                "hostname": hostname,
                "created_by": username
            }

    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Cloudflare API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating tunnel: {str(e)}")


@app.delete("/tunnels/{customer_id}")
async def delete_tunnel(
    customer_id: str,
    username: str = Depends(verify_master_admin)
):
    """Delete a Cloudflare tunnel. Requires admin authentication."""

    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ACCOUNT_ID:
        raise HTTPException(
            status_code=500,
            detail="Cloudflare API not configured"
        )

    try:
        # Get tunnel info from database
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT tunnel_id, tunnel_name FROM tunnels WHERE customer_id = ?",
                (customer_id,)
            )
            row = await cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail=f"Tunnel for '{customer_id}' not found in database")

            tunnel_id = row[0]
            tunnel_name = row[1]

        # Delete tunnel from Cloudflare
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }

            delete_response = await client.delete(
                f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/{tunnel_id}",
                headers=headers
            )

            if delete_response.status_code not in [200, 204]:
                # Tunnel might already be deleted in Cloudflare, continue anyway
                print(f"Warning: Cloudflare delete returned {delete_response.status_code}")

        # Delete from database
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("DELETE FROM tunnels WHERE customer_id = ?", (customer_id,))
            await db.commit()

        return {
            "success": True,
            "message": f"Tunnel '{tunnel_name}' deleted successfully",
            "deleted_by": username
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting tunnel: {str(e)}")


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

        # Fetch all licenses
        cursor = await db.execute("""
            SELECT * FROM licenses
            ORDER BY created_at DESC
        """)
        licenses_rows = await cursor.fetchall()
        licenses = [dict(row) for row in licenses_rows]

        # Fetch all tunnels
        cursor = await db.execute("""
            SELECT * FROM tunnels
            ORDER BY created_at DESC
        """)
        tunnels_rows = await cursor.fetchall()
        tunnels = [dict(row) for row in tunnels_rows]

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "instances": instances,
        "total_customers": total_customers,
        "healthy_count": healthy_count,
        "outdated_count": outdated_count,
        "recent_updates": recent_updates,
        "licenses": licenses,
        "tunnels": tunnels,
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
