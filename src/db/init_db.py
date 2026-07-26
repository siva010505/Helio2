import os
import sys

# Allow running directly from project root or from src/db/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.db.db import engine
from src.db.models import Base


def init_db():
    print("Initializing SQLite database...")
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)

    # Safely migrate existing databases carried over from Helio 1
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            for col_sql in [
                "ALTER TABLE topics ADD COLUMN source_context_json TEXT;",
                "ALTER TABLE topics ADD COLUMN score_breakdown_json TEXT;",
            ]:
                try:
                    conn.execute(text(col_sql))
                except Exception:
                    pass
    except Exception:
        pass
    print("Database initialization complete. Tables:")
    for table_name in Base.metadata.tables.keys():
        print(f"  [OK] {table_name}")


if __name__ == "__main__":
    init_db()
