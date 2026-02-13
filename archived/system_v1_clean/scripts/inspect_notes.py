import os
import sys
from sqlalchemy import create_engine, text

# Use the environment variable
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("❌ DATABASE_URL is not set!")
    sys.exit(1)

# Force sync driver
if "aiomysql" in db_url:
    db_url = db_url.replace("mysql+aiomysql", "mysql+mysqlconnector")

print(f"🔌 Connecting to: {db_url.split('@')[-1]}")

try:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        print("🔍 Inspecting 'ai_schema_note' table...")
        try:
            # Check columns first
            insp = create_engine(db_url)
            inspector = inspect(conn)
            columns = inspector.get_columns('ai_schema_note')
            for col in columns:
                print(f"Column: {col['name']} ({col['type']})")
            else:
                print(f"✅ Found {len(rows)} sample rows:")
                for row in rows:
                    print(row)
        except Exception as e:
            print(f"❌ Failed to query ai_schema_note: {e}")

except Exception as e:
    print(f"❌ Connection failed: {e}")
