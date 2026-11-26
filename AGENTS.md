# AGENTS.md â€” imobi-scrapper

Repository guidelines for coding agents (**OpenCode**, **Gemini**, **Oh My Pi (OMP)**, and compatible tools).

## Role & Persona

You are a senior Python and DevOps engineer maintaining the `imobi-scrapper` real estate aggregation application and its deployment stack. Prioritize correctness, minimal focused diffs, security, and backward compatibility with existing backups and migrations.

## Available Tools & Commands

Use the workspace tools and repository commands below depending on the task:

- **`git`:** Inspect repository state (`git status`, `git diff`, `git log`) and create isolated feature branches off `origin/main`.
- **`ruff check .`:** Verify Python lint and formatting rules across the repository before finishing code edits.
- **`pytest -q`:** Run the offline contract and web test suite against temporary SQLite databases.
- **`alembic`:** Inspect or generate database schema revisions under `migrations/versions/` when models change.
- **`make` / `docker compose` / `podman-compose`:** Build container images and run local compose stacks (`make test`, `make lint`, `make build`, `make up`).
- **`scripts/backup.sh`:** Perform Postgres custom-format dumps and guarded restores inside the database container.

## 1. Git & Branching Workflow

- **One branch per feature or fix:** Create a separate branch for each new feature, refactor, or unrelated fix rather than reusing an existing feature branch or Pull Request (PR), unless the user explicitly asks to stack changes.
- **Start from `origin/main`:** Fetch remote refs and branch from `origin/main` before making commits (unless continuing an explicit follow-up on the active PR branch):

  ```bash
  git fetch origin
  git checkout -b <type>/<short-slug> origin/main
  ```

  Use branch prefixes such as `feat/<slug>`, `fix/<slug>`, `refactor/<slug>`, `chore/<slug>`, or `docs/<slug>`.
- **Protected `main`:** Avoid committing or pushing directly to `main`, and avoid force-pushing (`--force` or `-f`), unless explicitly confirmed by the user.
- **Conventional Commits:** Format commit messages with Conventional Commits (`feat:`, `fix:`, `chore(deps):`, `refactor:`, `docs:`, `test:`), because `release-please` (`release-please-config.json`) automates Semantic Versioning (SemVer) bumps and `CHANGELOG.md`. Omit `!` or `BREAKING CHANGE:` unless a major version bump is explicitly intended.

## 2. Naming & Domain Conventions

- **Repository vs. current runtime names:** The GitHub repository is `imobi-scrapper`, while the current packaged Python distribution in `pyproject.toml` is `imovel-scrapper` (wheel `imovel_scrapper-*.whl`).
- **Current Python package & Command-Line Interface (CLI):** The package lives under `src/imovel/` and exposes the `imovel` command (`imovel seed`, `imovel scrape`, `imovel serve`, `imovel check-links`, `imovel probe`, `imovel sources`, `imovel types`, `imovel stats`, `imovel duplicates`).
- **Current Compose stack, containers, volumes, networks, and Database (DB) defaults:** `imovel` (`imovel-db`, `imovel-app`, `imovel-scraper`, `imovel_pgdata`, `imovel_state`, `imovel_db`, `imovel_egress`). If a dedicated PR migrates runtime identifiers toward `imobi` / `imobi-scrapper`, update packaging, Docker, Continuous Integration (CI), and Compose files together unless the user specifies a narrower scope.
- **Intentional exceptions (keep as-is unless explicitly requested):**
  - Web listing detail routes `/imovel/{listing_id}` and `/imovel/{listing_id}/extra` (Portuguese domain noun).
  - Third-party portals/platforms and their identifiers (`Imovelweb`, `imovelweb`, `imovelweb.com.br`, `imgbr.imovelwebcdn.com`, `Imoview`, `imoview_api`, `opcaoimovel`, `codigoOpcaoimovel`).
  - External listing URLs in scrapers/tests and captured markup under `tests/fixtures/**`.

## 3. Database, Migrations & Cross-Version Backup Compatibility

- **Bidirectional `.dump` compatibility:** Keep `.dump` backups mutually restorable across versions (including `imovel-*.dump` archives), unless an explicit breaking migration plan is approved by the user.
- **Role/DB-agnostic dumps:** Keep `--no-owner --no-privileges` on `pg_dump` and `pg_restore` in `src/imovel/backup.py` (and `pg_restore --dbname <current_db>` without `--create` in both `src/imovel/backup.py` and `scripts/backup.sh`), unless a deployment explicitly requires preserving role ownership.
- **Safe restore lifecycle:**
  - Keep `SAFETY_PREFIX = "pre-restore-"` unchanged unless migrating stored safety-dump filenames in the same change.
  - Avoid pruning archives during safety-backup creation before `pg_restore` in `scripts/backup.sh` so older target files are not deleted before restoration, unless `BACKUP_KEEP` retention behavior is intentionally redesigned.
  - Preserve historical Alembic revision IDs in `migrations/versions/` (`2ba8063dcf2f`, `b49c856601e2`, `2fe94b75cbe0`, `f9b6f6f1a020`) unless rewriting migration history is explicitly approved.
  - Keep `seed()` idempotent so existing `triage` or `criteria` rows are preserved, unless a reset command is explicitly requested.
- **Test coverage:** Include regression tests in `tests/test_web.py` when modifying models, migrations, or backup/restore behavior, unless no code paths are affected (for example, pure documentation edits).

## 4. Dependency Pinning & CI/Docker Security

- **Synchronized exact pins (`==`):** Keep exact dependency versions aligned across `pyproject.toml`, `requirements.txt`, `Dockerfile`, and `.github/workflows/ci.yml` unless a dependency is strictly dev-only (`pytest`, `ruff`).
- **Binary-only installs:** `Dockerfile` and `.github/workflows/ci.yml` install dependencies with `--only-binary :all:` on Python 3.13; keep inline pins in both files (static security analyzers cannot resolve `-r` files) and verify PyPI wheel availability when bumping versions.
- **GitHub Actions hardening:**
  - Pin each action (`uses:`) to a 40-character Secure Hash Algorithm (SHA) commit hash with a `# vX.Y.Z` version comment unless testing an unpinned internal prototype approved by the user.
  - Keep top-level `permissions: {}` and grant minimal per-job permissions.
  - Set `persist-credentials: false` on `actions/checkout` unless a subsequent step explicitly needs persisted git credentials.
  - Avoid `sudo`, hardcoded passwords/tokens, or direct `${{ ... }}` interpolation of untrusted inputs inside `run:` scripts unless explicitly authorized for a specific runner requirement.

## 5. Code Quality & Verification

- **Lint & style:** Follow Ruff (`ruff check .`, `target-version = "py312"`, `line-length = 110`, rules `E` (`pycodestyle`), `F` (`Pyflakes`), `I` (`isort`), `pyupgrade (UP)`, `B` (`flake8-bugbear`)) and Python Enhancement Proposal (PEP) 257 docstrings (`setup.cfg`).
- **Static analysis & security:**
  - Compare passwords, tokens, and Cross-Site Request Forgery (CSRF) tokens using `secrets.compare_digest`.
  - Build dynamic Structured Query Language (SQL) identifiers safely with `psycopg.sql.Identifier` and `sql.Literal`.
  - Apply restrictive permissions (`0o700`) on temporary executable scripts and generate test credentials dynamically via `secrets.token_hex`.
  - Keep cognitive complexity low by extracting focused helpers.
- **Line endings:** Keep Line Feed (LF, `\n`) endings per `.gitattributes` (critical for `scripts/backup.sh` and `.github/workflows/*.yml`).
- **Tests:** Run `pytest -q` for the offline suite before finalizing code changes (keep network tests marked with `@pytest.mark.live`); if local tooling is unavailable in the environment, report the gap clearly instead of claiming tests passed.
