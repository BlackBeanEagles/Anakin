"""Container healthcheck. Its own file rather than an inline `python -c`, because
that needed nested quotes inside a Dockerfile string and would have broken silently.

Exit 0 = healthy. Anything else and the platform restarts the container.
"""
import os
import sys
import urllib.request

url = f"http://127.0.0.1:{os.getenv('PORT', '8000')}/health"

try:
    with urllib.request.urlopen(url, timeout=5) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f"healthcheck failed: {exc}", file=sys.stderr)
    sys.exit(1)
