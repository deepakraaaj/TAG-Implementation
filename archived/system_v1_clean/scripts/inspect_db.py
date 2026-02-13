from sqlalchemy import create_engine, inspect
from app.config import get_settings

settings = get_settings()
# Handle async driver fix for inspection
db_url = settings.DATABASE_URL.replace("mysql+aiomysql", "mysql+mysqlconnector")
engine = create_engine(db_url)
inspector = inspect(engine)

table = "task_transaction"
try:
    cols = inspector.get_columns(table)
    print(f"--- Schema for {table} ---")
    for col in cols:
        print(f"{col['name']} ({col['type']})")
except Exception as e:
    print(f"Error inspecting {table}: {e}")
    print("Available tables:", inspector.get_table_names())
