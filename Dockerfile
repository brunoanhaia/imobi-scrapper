# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Sao_Paulo \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libatomic1 tzdata \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: pinned runtime dependencies, wheels only (no build scripts).
COPY requirements.lock ./
RUN python -m venv /app/.venv \
    && pip install --upgrade pip \
    && pip install --only-binary :all: --no-cache-dir -r requirements.lock

# Layer 2: Chromium + system libraries for the WAF-protected portals.
RUN playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

# Layer 3: application code and packaging (changes on every commit).
COPY pyproject.toml README.md LICENSE alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-deps .

COPY .env.example ./

RUN useradd --create-home --uid 10001 imovel \
    && mkdir -p /app/data /app/.cache/img \
    && chown -R imovel:imovel /app
USER imovel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["imovel", "serve"]
