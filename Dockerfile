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

# Layer 1: dependencies only, so source edits do not invalidate it.
COPY pyproject.toml README.md LICENSE ./
RUN python -m venv /app/.venv \
    && pip install --upgrade pip \
    && python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

# Layer 2: Chromium + system libs (cached unless the base or deps change).
RUN playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

# Layer 3: application code and packaging (changes on every commit).
COPY alembic.ini ./
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
