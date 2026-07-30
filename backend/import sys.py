import sys
from pathlib import Path
from sqlalchemy import text

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import database
import models

def migrate_fresh():
    print("[MIGRATION] Resetting & Creating Tables...")
    try:
        # Create missing tables freshly (messages, notifications, users, connections)
        models.Base.metadata.create_all(bind=database.engine)
        print("[SUCCESS] All tables (messages, notifications, connections, users) created cleanly!")
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")

if __name__ == "__main__":
    migrate_fresh()