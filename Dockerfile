# The hosted demo: the bridge and its own FalkorDB in one container. Every visitor gets their own virtual
# key and graph (PURRSONA_HOSTED=1, see api/purrsona/visitors.py). Give it ANTHROPIC_API_KEY as a secret.
# Locally you don't need this: see "Try it" in the README.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY api/pyproject.toml api/uv.lock api/
RUN cd api && uv sync --frozen --no-dev \
 && uv pip install --python /opt/venv/bin/python falkordblite==0.10.0
COPY api/purrsona api/purrsona
COPY data data
COPY extension extension
ENV PURRSONA_HOSTED=1 \
    PURRSONA_EMBEDDED_DB=/tmp/purrsona/falkordb.rdb \
    PURRSONA_KEYS_DIR=/tmp/purrsona/keys \
    PURRSONA_TRUST_DIR=/tmp/purrsona/trust \
    PORT=7860
EXPOSE 7860
WORKDIR /app/api
CMD ["sh", "-c", "mkdir -p /tmp/purrsona/keys && exec /opt/venv/bin/uvicorn purrsona.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 2"]
