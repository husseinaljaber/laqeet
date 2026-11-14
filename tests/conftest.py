import os
import tempfile

# tests use their own database, never data/laqeet.db
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("ADMIN_TOKEN", "test-token")
