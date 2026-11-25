# AGENTS.md â€” imobi-scrapper

Repository guidelines for AI coding agents (**OpenCode**, **Gemini**, **OMP**, and compatible tools).

## 1. Git & Branching Workflow (Mandatory)

- **One branch per feature/fix:** Never commit a new feature, refactor, or unrelated change onto an existing feature or fix branch/PR. Always start from the latest `origin/main`:
  ```bash
  git fetch origin
  git checkout -b <type>/<short-slug> origin/main
  ```
  Use branch prefixes such as `feat/<slug>`, `fix/<slug>`, `refactor/<slug>`, `chore/<slug>`, or `docs/<slug>`.
- **Protected `main`:** Never commit or push directly to `main`, and never force-push (`--force` / `-f`) without explicit user confirmation.
- **Conventional Commits:** Every commit must follow Conventional Commits (`feat:`, `fix:`, `chore(deps):`, `refactor:`, `docs:`, `test:`), because `release-please` (`release-please-config.json`) automates SemVer bumps and `CHANGELOG.md`. Avoid `!` or `BREAKING CHANGE:` unless a major version bump is explicitly intended.

## 2. Naming & Domain Conventions

- **Project, package, and local container image:** `imobi-scrapper` (built wheel `imobi_scrapper-*.whl`).
- **Python package & CLI command:** `imobi` (`src/imobi/`, `imobi seed`, `imobi scrape`, `imobi serve`, `imobi check-links`, `imobi probe`, `imobi sources`, `imobi types`, `imobi stats`, `imobi duplicates`).
- **Compose stack, containers, volumes, networks, and DB defaults:** `imobi` (`imobi-db`, `imobi-app`, `imobi-scraper`, `imobi_pgdata`, `imobi_state`, `imobi_db`, `imobi_egress`).
- **Intentional exceptions (do NOT rename):**
  - Web listing detail routes `/imovel/{listing_id}` and `/imovel/{listing_id}/extra` (Portuguese domain noun).
  - Third-party portals/platforms and their identifiers (`Imovelweb`, `imovelweb`, `imovelweb.com.br`, `imgbr.imovelwebcdn.com`, `Imoview`, `imoview_api`, `opcaoimovel`, `codigoOpcaoimovel`).
  - External listing URLs in scrapers/tests and captured markup under `tests/fixtures/**`.

## 3. Database, Migrations & Cross-Version Backup Compatibility

- **Bidirectional `.dump` compatibility:** Backups must remain mutually restorable across versions (including legacy `imovel-*.dump` archives from `v0.2.0` and `imobi-*.dump` archives).
- **Role/DB-agnostic dumps:** Both `src/imobi/backup.py` and `scripts/backup.sh` must always pass `--no-owner --no-privileges` to `pg_dump` and `pg_restore`, restoring into `--dbname <current_db>` without `--create`.
- **Safe restore lifecycle:**
  - Keep `SAFETY_PREFIX = "pre-restore-"` unchanged.
  - In `scripts/backup.sh`, never run `prune` while creating the safety dump before `pg_restore` (so older target archives are not deleted before being restored).
  - Never alter historical Alembic revision IDs in `migrations/versions/` (`2ba8063dcf2f`, `b49c856601e2`, `2fe94b75cbe0`, `f9b6f6f1a020`).
  - Keep `seed()` idempotent: never overwrite existing `triage` or `criteria` rows.
- **Required test coverage:** Changes to models, migrations, or backup/restore logic must include tests in `tests/test_web.py`, `tests/test_backup_script.py`, and `tests/test_postgres_backup.py`.

## 4. Dependency Pinning & CI/Docker Security

- **Synchronized exact pins (`==`):** Keep exact dependency versions synchronized across all four files:
  - `pyproject.toml`
  - `requirements.txt`
  - `Dockerfile`
  - `.github/workflows/ci.yml`
- **Binary-only installs:** `Dockerfile` and `.github/workflows/ci.yml` install dependencies with `--only-binary :all:` on Python 3.13. Keep inline pins in both files (static security analyzers cannot resolve `-r` files) and verify PyPI wheel availability when bumping versions.
- **GitHub Actions hardening:**
  - Pin every action (`uses:`) to a 40-character commit SHA with a `# vX.Y.Z` version comment.
  - Keep top-level `permissions: {}` and grant minimal per-job permissions.
  - Set `persist-credentials: false` on `actions/checkout`.
  - Avoid `sudo` and hardcoded passwords/tokens in workflows; never interpolate untrusted `${{ ... }}` expressions directly inside `run:` scripts.

## 5. Code Quality & Verification

- **Lint & style:** Follow Ruff (`ruff check .`, `target-version = "py312"`, `line-length = 110`, rules `E, F, I, UP, B`) and PEP 257 docstrings (`setup.cfg`).
- **Static analysis & security (SonarCloud / Codacy / Bandit / Hadolint):**
  - Use `secrets.compare_digest` for password/token/CSRF comparisons.
  - Use `psycopg.sql.Identifier` / `sql.Literal` for dynamic SQL identifiers.
  - Use restrictive permissions (`0o700`) on temporary executable scripts; generate test credentials dynamically via `secrets.token_hex`.
  - Keep cognitive complexity low by extracting focused helpers.
- **Line endings:** Use LF (`\n`) per `.gitattributes` (critical for `scripts/backup.sh` and `.github/workflows/*.yml`).
- **Tests:** Run `pytest -q` for the offline suite (tests hitting the network must carry `@pytest.mark.live`). Real PostgreSQL 16 backup compatibility tests run when `TEST_POSTGRES_ADMIN_DSN` is set.
