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

# Dependencies are pinned inline (the analyzer cannot see pins inside -r files) and installed
# as wheels only. Chromium lives in the same layer so the RUN sequence is a single step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libatomic1 tzdata \
    && python -m venv /app/.venv \
    && pip install --upgrade "pip==25.1.1" \
    && pip install --only-binary :all: --no-cache-dir \
        alembic==1.20.1 \
        brotli==1.2.1 \
        fastapi==0.163.1 \
        httpx==0.28.1 \
        itsdangerous==2.2.0 \
        jinja2==3.1.6 \
        playwright==1.65.2 \
        psycopg[binary]==3.3.9 \
        pydantic-settings==2.16.3 \
        pydantic==2.14.3 \
        python-multipart==0.0.33 \
        selectolax==1.0.2 \
        sqladmin==0.32.0 \
        sqlalchemy[asyncio]==2.1.8 \
        tenacity==9.2.1 \
        typer==0.29.1 \
        uvicorn[standard]==0.54.0 \
    && playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

# Application code and packaging (changes on every commit).
COPY pyproject.toml README.md LICENSE alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-deps --no-build-isolation .

COPY .env.example ./

RUN useradd --create-home --uid 10001 imovel \
    && mkdir -p /app/data /app/.cache/img \
    && chown -R imovel:imovel /app
USER imovel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["imovel", "serve"]
