import os
import sys

# Ensure the ship_log package root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Point to an in-memory SQLite DB so tests never touch the real data file
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("UPLOAD_DIR", "/tmp/ship_log_test_uploads")
