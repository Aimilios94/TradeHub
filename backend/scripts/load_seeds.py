"""Load db/seeds/*.json into PostgreSQL. Usage: python -m scripts.load_seeds"""

from app.config import get_settings
from app.db import get_engine
from app.seeds import load_seeds

if __name__ == "__main__":
    for table, count in load_seeds(get_engine(), get_settings().seeds_dir).items():
        print(f"seeded {table}: {count} rows")
