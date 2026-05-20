"""Entry point — works locally and on Railway."""
import os

# ── SSL certificates (needed on macOS; no-op on Linux) ──────────────────────
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

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
        reload=False,
        access_log=True,
    )
