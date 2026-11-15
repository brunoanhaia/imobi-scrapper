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
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /app/.venv \
    && pip install --upgrade pip \
    && pip install .

# Chromium + its shared libraries, for the WAF-protected portals (VivaReal, ZAP, OLX, Imovelweb).
RUN playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

COPY .env.example ./

RUN useradd --create-home --uid 10001 imovel \
    && mkdir -p /app/data \
    && chown -R imovel:imovel /app
USER imovel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["imovel", "serve"]
