#!/usr/bin/env python3
"""
Database Connection Verification Script
Tests all remote database connections and displays available tables
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load environment variables
load_dotenv(REPO_ROOT / ".env")

from app.db.multi_tenant_manager import MultiTenantDatabaseManager


async def verify_all_connections():
    """Verify and display all database connections"""
    print("\n" + "=" * 70)
    print("🔍 MULTI-TENANT DATABASE VERIFICATION")
    print("=" * 70)
    
    databases = await MultiTenantDatabaseManager.list_available_databases()
    results = {
        "✅ Connected": [],
        "❌ Failed": [],
    }
    
    for app_id, description in databases.items():
        print(f"\n🔗 Testing {app_id.upper()}: {description}")
        is_connected = await MultiTenantDatabaseManager.verify_connection(app_id)
        
        if is_connected:
            results["✅ Connected"].append(app_id)
            print(f"   ✅ Connection successful")
            
            # Get database info
            try:
                conn = await MultiTenantDatabaseManager.get_connection(app_id)
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
                    table_count = await cursor.fetchone()
                    print(f"   📊 Tables: {table_count[0]}")
                    
                    await cursor.execute("SELECT DATABASE()")
                    db_name = await cursor.fetchone()
                    print(f"   🗄️  Database: {db_name[0]}")
                
                conn.close()
            except Exception as e:
                print(f"   ⚠️  Could not fetch details: {e}")
        else:
            results["❌ Failed"].append(app_id)
            print(f"   ❌ Connection failed")
    
    # Summary
    print("\n" + "=" * 70)
    print("📋 SUMMARY")
    print("=" * 70)
    print(f"✅ Connected:  {len(results['✅ Connected'])}/{len(databases)}")
    for app_id in results["✅ Connected"]:
        print(f"   • {app_id}")
    
    if results["❌ Failed"]:
        print(f"\n❌ Failed: {len(results['❌ Failed'])}/{len(databases)}")
        for app_id in results["❌ Failed"]:
            print(f"   • {app_id}")
    
    print("\n" + "=" * 70)
    
    await MultiTenantDatabaseManager.close_all_connections()


if __name__ == "__main__":
    asyncio.run(verify_all_connections())
