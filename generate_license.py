#!/usr/bin/env python3
"""
License Key Generator for HushLane
Run this to create license keys for new customers
"""
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta


def generate_license_key():
    """Generate a secure license key."""
    # Format: HL-XXXX-XXXX-XXXX-XXXX
    parts = [secrets.token_hex(4).upper() for _ in range(4)]
    return f"HL-{'-'.join(parts)}"


def create_license(customer_id, customer_name, plan="standard", months=12):
    """
    Create a new license for a customer.

    Args:
        customer_id: Unique customer identifier (e.g., 'acme')
        customer_name: Customer's company name (e.g., 'Acme Corp')
        plan: License plan (standard, pro, enterprise)
        months: License duration in months (None for lifetime)
    """
    db_path = "instances.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if customer already has a license
        cursor.execute("SELECT license_key FROM licenses WHERE customer_id = ?", (customer_id,))
        existing = cursor.fetchone()

        if existing:
            print(f"❌ Error: Customer '{customer_id}' already has a license: {existing[0]}")
            print(f"   Use revoke_license.py to revoke it first, or update_license.py to extend.")
            conn.close()
            return None

        # Generate new license key
        license_key = generate_license_key()

        # Calculate expiration
        expires_at = None
        if months:
            expires_at = (datetime.now() + timedelta(days=months*30)).isoformat()

        # Insert license
        cursor.execute("""
            INSERT INTO licenses (license_key, customer_id, customer_name, plan, status, expires_at)
            VALUES (?, ?, ?, ?, 'active', ?)
        """, (license_key, customer_id, customer_name, plan, expires_at))

        conn.commit()
        conn.close()

        print("\n" + "="*70)
        print("✅ LICENSE CREATED SUCCESSFULLY")
        print("="*70)
        print(f"\nCustomer ID:   {customer_id}")
        print(f"Customer Name: {customer_name}")
        print(f"Plan:          {plan}")
        print(f"Status:        active")
        if expires_at:
            print(f"Expires:       {datetime.fromisoformat(expires_at).strftime('%Y-%m-%d')} ({months} months)")
        else:
            print(f"Expires:       Never (lifetime)")
        print(f"\n🔑 LICENSE KEY:")
        print(f"   {license_key}")
        print("\n" + "="*70)
        print("\n📧 Send this to customer:")
        print(f"\n   Welcome to HushLane!")
        print(f"   Your License Key: {license_key}")
        print(f"   Customer ID: {customer_id}")
        print(f"\n   Add this to your .env file:")
        print(f"   LICENSE_KEY={license_key}")
        print(f"   CUSTOMER_ID={customer_id}")
        print("\n" + "="*70 + "\n")

        return license_key

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def list_licenses():
    """List all licenses."""
    db_path = "instances.db"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT license_key, customer_id, customer_name, plan, status,
                   created_at, expires_at, last_validated
            FROM licenses
            ORDER BY created_at DESC
        """)

        licenses = cursor.fetchall()
        conn.close()

        if not licenses:
            print("\n📋 No licenses found\n")
            return

        print("\n" + "="*120)
        print("📋 ALL LICENSES")
        print("="*120)
        print(f"{'Customer ID':<15} {'Customer Name':<25} {'Plan':<12} {'Status':<10} {'Expires':<12} {'License Key':<20}")
        print("-"*120)

        for lic in licenses:
            expires = "Never"
            if lic['expires_at']:
                exp_date = datetime.fromisoformat(lic['expires_at'])
                if exp_date < datetime.now():
                    expires = f"EXPIRED {exp_date.strftime('%Y-%m-%d')}"
                else:
                    days_left = (exp_date - datetime.now()).days
                    expires = f"{exp_date.strftime('%Y-%m-%d')} ({days_left}d)"

            print(f"{lic['customer_id']:<15} {lic['customer_name']:<25} {lic['plan']:<12} {lic['status']:<10} {expires:<12} {lic['license_key']:<20}")

        print("="*120 + "\n")

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")


def main():
    """Main CLI interface."""
    import argparse

    parser = argparse.ArgumentParser(description="HushLane License Generator")
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Create license command
    create_parser = subparsers.add_parser('create', help='Create a new license')
    create_parser.add_argument('customer_id', help='Customer ID (e.g., acme)')
    create_parser.add_argument('customer_name', help='Customer name (e.g., "Acme Corp")')
    create_parser.add_argument('--plan', default='standard', choices=['standard', 'pro', 'enterprise'], help='License plan')
    create_parser.add_argument('--months', type=int, default=12, help='License duration in months (0 for lifetime)')

    # List licenses command
    list_parser = subparsers.add_parser('list', help='List all licenses')

    args = parser.parse_args()

    if args.command == 'create':
        months = None if args.months == 0 else args.months
        create_license(args.customer_id, args.customer_name, args.plan, months)
    elif args.command == 'list':
        list_licenses()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
