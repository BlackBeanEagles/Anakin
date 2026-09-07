# Persist — production image.
#
# ONE WORKER, DELIBERATELY. The escalation tick runs in-process via APScheduler and
# the intake rate limiter is in memory, so a second worker would double-advance cases
# and halve the rate limit. Scale by making the box bigger, not by adding workers.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY evals/ ./evals/
COPY scripts/ ./scripts/
COPY run.py ./

# Cases, and the dry-run outbox, live here. Mount a persistent volume at this path
# or the ledger resets on every deploy.
ENV PERSIST_DATA_DIR=/data
RUN mkdir -p /data
VOLUME ["/data"]

ENV HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

COPY healthcheck.py ./
HEALTHCHECK --interval=60s --timeout=6s --start-period=25s --retries=3 \
  CMD ["python", "healthcheck.py"]

CMD ["python", "run.py"]
