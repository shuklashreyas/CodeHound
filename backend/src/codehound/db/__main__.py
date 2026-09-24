"""Apply schema migrations using CODEHOUND_DATABASE_URL (SQLite locally by default)."""

import argparse

from codehound.db.database import Database

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("command", choices=["upgrade"])
parser.parse_args()
database = Database()
try:
    database.migrate()
    print("Database schema is up to date.")
finally:
    database.close()
