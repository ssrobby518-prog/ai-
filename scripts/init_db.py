"""Initialize the SQLite database with all required tables."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DB_PATH
from core.storage import init_db


def main() -> None:
    print(f"Initializing database at: {DB_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)
    print("Database initialized successfully.")
    print("  Tables: items, ai_results, dedup_cache")


if __name__ == "__main__":
    main()
