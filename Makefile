.PHONY: help test lint build up down clean refresh dev e2e backup backups restore logs

help: ## show the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

test: ## run the offline test suite
	.venv/bin/pytest -q

lint: ## ruff check
	.venv/bin/ruff check .

build: ## rebuild the image
	TMPDIR=$$PWD/.tmp podman build -q -t imovel-scrapper:latest .

up: ## start db + app + scraper
	podman-compose up -d

down: ## stop everything
	podman-compose down

dev: ## run the web UI locally against the compose database (no image build)
	DATABASE_URL=postgresql+psycopg://imovel:imovel@127.0.0.1:5432/imovel .venv/bin/imovel serve --port 8080

e2e: ## sandbox server for browser tests (throwaway database imovel_e2e, port 8081)
	.venv/bin/python scripts/e2e.py --port 8081

backup: ## dump the database into ./backups right now
	podman-compose run --rm backup imovel backup

backups: ## list existing backups
	podman-compose run --rm backup imovel backups

restore: ## restore a backup: make restore FILE=backups/imovel-....dump
	podman-compose run --rm backup imovel restore /backups/$(FILE) --yes

logs: ## follow the collector logs
	podman logs -f imovel-scrapper_scraper_1

refresh: ## recreate the containers on the new image, then prune the old one
	podman-compose up -d --force-recreate
	podman image prune -f
	podman builder prune -f
	@podman images --format "{{.Repository}}:{{.Tag}} {{.Size}}"

clean: ## remove dangling images and build cache (frees GBs after rebuilds)
	podman image prune -f
	podman builder prune -f
	@podman system df --format "{{.Type}} size={{.Size}} reclaimable={{.Reclaimable}}"
