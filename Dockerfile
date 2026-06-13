# Dockerfile — runs on arm64 (Raspberry Pi) and amd64 alike
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/
COPY config/ ./config/

ENV DB_PATH=/data/trading.db

# Drop root: run as an unprivileged user that owns /app and the data volume.
# (A fresh named volume inherits /data's ownership from the image.)
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
VOLUME ["/data"]
USER appuser

# One-shot daily run; the scheduler (cron/compose) invokes this.
CMD ["uv", "run", "python", "-m", "trading.run"]
