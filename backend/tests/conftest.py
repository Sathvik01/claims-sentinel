import os
import tempfile
from pathlib import Path

# Isolated database/uploads for the test session — set before `app` is imported.
_tmp = Path(tempfile.mkdtemp(prefix="claims-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["GROQ_API_KEY"] = ""


def auth_headers(client, username: str) -> dict:
    from app.auth.permissions import DEMO_PASSWORD

    r = client.post("/api/auth/token", data={"username": username, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
