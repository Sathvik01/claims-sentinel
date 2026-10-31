import os
import tempfile
from pathlib import Path

# Isolated database/uploads for the test session — set before `app` is imported.
_tmp = Path(tempfile.mkdtemp(prefix="claims-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["GROQ_API_KEY"] = ""
