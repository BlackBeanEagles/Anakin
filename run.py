"""Start Persist.

    python run.py                 local, http://127.0.0.1:8000
    HOST=0.0.0.0 PORT=8000 python run.py     for a host that injects $PORT

Most PaaS hosts (Render, Railway, Fly) set PORT and require binding 0.0.0.0, so this
reads both from the environment rather than hardcoding localhost.
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "").lower() in {"1", "true", "yes"},
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
