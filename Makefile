.PHONY: help test lint build up down clean refresh logs

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
