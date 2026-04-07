#!/usr/bin/env python3
"""
End-to-End Test: Company Loading for All Apps
Tests the full flow from app registry to database query
"""

import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from app.config import Settings
from app.apps.registry import AppRegistry
from app.services.data.schema_service import SchemaService
from sqlalchemy import create_engine, text


def test_app_registry():
    """Test app registry loads correctly"""
    print("\n" + "="*70)
    print("1️⃣  TESTING APP REGISTRY")
    print("="*70)
    
    settings = Settings()
    registry = AppRegistry.from_settings(settings)
    
    print(f"✅ Registry initialized")
    print(f"   Enabled: {registry.enabled()}")
    print(f"   Apps: {len(list(registry.list_apps()))}")
    print(f"   Default: {registry.default_app_id}")
    
    apps = list(registry.list_apps())
    assert registry.enabled(), "Registry not enabled!"
    assert apps, "Expected at least one configured app"
    expected_default = str(settings.DEFAULT_CHAT_APP_ID or "").strip()
    if expected_default:
        assert registry.default_app_id == expected_default, (
            f"Expected default app '{expected_default}', got {registry.default_app_id}"
        )
    
    print("✅ All assertions passed!")
    return settings, registry


def test_database_urls(registry):
    """Test database URLs are valid and accessible"""
    print("\n" + "="*70)
    print("2️⃣  TESTING DATABASE URLS")
    print("="*70)
    
    for app_id, app_config in registry.list_apps():
        db_url = app_config.database_url
        assert db_url, f"No database URL for {app_id}"
        assert "mysql" in db_url, f"Invalid database URL for {app_id}: {db_url[:50]}"
        print(f"✅ {app_id}: URL valid")
    
    print("✅ All database URLs valid!")


def test_schema_service_and_queries(registry):
    """Test schema service can inspect tables and execute queries"""
    print("\n" + "="*70)
    print("3️⃣  TESTING SCHEMA SERVICE & COMPANY QUERIES")
    print("="*70)
    
    settings = Settings()
    schema_service = SchemaService(db_url=settings.DATABASE_URL)
    results = {}
    
    for app_id, app_config in registry.list_apps():
        try:
            # Get company table columns
            columns = schema_service.get_table_columns(['company'], db_url=app_config.database_url)
            company_columns = {str(c or '').strip().lower() for c in (columns.get('company') or set()) if str(c or '').strip()}
            
            if 'id' not in company_columns:
                results[app_id] = "❌ No 'id' column"
                continue
            
            # Build query same as endpoint
            name_candidates = {'name', 'company_name', 'display_name', 'title'}
            name_column = next((c for c in name_candidates if c in company_columns), None)
            active_column = "is_active" if "is_active" in company_columns else ""
            
            select_parts = ["company.id AS company_id"]
            if name_column:
                select_parts.append(f"TRIM(COALESCE(company.{name_column}, '')) AS company_name")
            else:
                select_parts.append("CAST(company.id AS CHAR) AS company_name")
            if active_column:
                select_parts.append(f"company.{active_column} AS is_active")
            
            order_by = "company_name ASC, company_id ASC" if name_column else "company_id ASC"
            sql = (
                "SELECT " + ", ".join(select_parts) +
                " FROM company ORDER BY " + order_by + " LIMIT 200"
            )
            
            # Convert URL for inspection
            inspection_url = app_config.database_url
            if "aiomysql" in inspection_url:
                inspection_url = inspection_url.replace("mysql+aiomysql", "mysql+mysqlconnector")
                blocked = {"allowPublicKeyRetrieval", "useSSL"}
                parsed = urlsplit(inspection_url)
                query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
                filtered_pairs = [(k, v) for (k, v) in query_pairs if k not in blocked]
                inspection_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(filtered_pairs), parsed.fragment))
            
            engine = create_engine(inspection_url, pool_pre_ping=True)
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                rows = result.mappings().all()
                results[app_id] = f"✅ {len(rows)} companies"
        
        except Exception as e:
            results[app_id] = f"❌ {type(e).__name__}: {str(e)[:50]}"
    
    # Print results
    for app_id, result in sorted(results.items()):
        print(f"{app_id:20} {result}")
    
    # Check all passed
    failed = [k for k, v in results.items() if v.startswith("❌")]
    assert not failed, f"Failed apps: {failed}"
    
    print("✅ All schema and query tests passed!")


def main():
    """Run all tests"""
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "END-TO-END COMPANY LOADING TEST" + " "*22 + "║")
    print("╚" + "="*68 + "╝")
    
    try:
        settings, registry = test_app_registry()
        test_database_urls(registry)
        test_schema_service_and_queries(registry)
        app_count = len(list(registry.list_apps()))
        
        print("\n" + "="*70)
        print("🎉 ALL TESTS PASSED! 🎉")
        print("="*70)
        print(f"\n✅ App registry loads {app_count} apps")
        print("✅ All database URLs configured")
        print("✅ Schema service works correctly")
        print("✅ Company queries execute successfully")
        print("✅ Frontend should now show companies!")
        print()
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
