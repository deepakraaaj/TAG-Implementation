from sqlalchemy import create_engine, inspect
import os

# Hardcoded from docker-compose/env for verification
DB_URL = "mysql+mysqlconnector://root:Root%4012345@127.0.0.1:3309/dev-fitss"

try:
    engine = create_engine(DB_URL)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"Total tables: {len(tables)}")
    print("First 10 tables:", tables[:10])
except Exception as e:
    print(f"Error: {e}")
