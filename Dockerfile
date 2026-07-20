# Container image for the Curriculum Coherence Layer.
# Build:  docker build -t coherence-layer .
# Run (SQLite on a mounted volume):
#   docker run -p 8000:8000 -e HOST=0.0.0.0 \
#     -e CCL_DB=/data/ccl.db -v coherence_data:/data \
#     -e ANTHROPIC_API_KEY=sk-...  coherence-layer
# Run (managed Postgres, e.g. Neon — no volume needed, data lives in the DB):
#   docker run -p 8000:8000 -e HOST=0.0.0.0 \
#     -e DATABASE_URL='postgresql://user:pass@host/db?sslmode=require' \
#     -e ANTHROPIC_API_KEY=sk-...  coherence-layer
#
# DATABASE_URL (Postgres) takes priority over CCL_DB (SQLite). HOST=0.0.0.0 makes
# it reachable outside the container. Set ANTHROPIC_API_KEY for the live model;
# omit it to run the offline stub.

FROM python:3.13-slim

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY ccl ./ccl

# Defaults suited to a container. PORT is respected if the host injects one.
ENV HOST=0.0.0.0 \
    PORT=8000 \
    CCL_DB=/data/ccl.db \
    PYTHONUNBUFFERED=1

# Persist the SQLite DB here; mount a volume to this path.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["python", "-m", "ccl.web"]
