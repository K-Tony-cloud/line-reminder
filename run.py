"""Entry point — works locally and on Railway."""
import os
import json
import tempfile

# ── SSL certificates (needed on macOS; no-op on Linux) ──────────────────────
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# ── Google credentials from env var (Railway) ────────────────────────────────
# On Railway, set GOOGLE_CREDENTIALS_JSON to the full JSON content of the
# service account key. Locally, the file at GOOGLE_CREDENTIALS_FILE is used.
_creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
if _creds_json:
    _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _tmp.write(_creds_json)
    _tmp.close()
    os.environ["GOOGLE_CREDENTIALS_FILE"] = _tmp.name

# ── Start server ─────────────────────────────────────────────────────────────
import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    port = int(os.environ.get("PORT", settings.app_port))
    dev = os.environ.get("ENVIRONMENT", "production") == "development"

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=port,
        reload=dev,        # reload=False in production (avoids double scheduler)
        workers=1,         # single worker — multiple workers would duplicate scheduler
        access_log=True,
    )
