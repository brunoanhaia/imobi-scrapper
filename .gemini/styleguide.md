# Gemini Code Assist Review Styleguide

When reviewing pull requests in this repository, enforce the project rules defined in `AGENTS.md`:

1. **Single-purpose branches & Conventional Commits:**
   - Flag PRs that mix unrelated features or fixes onto an existing branch.
   - Verify commits and PR titles follow Conventional Commits (`feat:`, `fix:`, `chore(deps):`, `refactor:`, `docs:`, `test:`) without unintended `BREAKING CHANGE` markers.

2. **Naming conventions & intentional exceptions:**
   - Verify consistency between packaging (`pyproject.toml`), CLI entrypoints (`src/imovel/` or `src/imobi/` when migrated), Docker, CI, and Compose files.
   - Do not flag intentional domain/external exceptions: `/imovel/{listing_id}` routes, `Imovelweb`, `Imoview`, `imoview_api`, `opcaoimovel`, external listing URLs, or `tests/fixtures/**`.

3. **Database, migrations & backup compatibility:**
   - Ensure `pg_dump` and `pg_restore` in both `backup.py` and `scripts/backup.sh` preserve `--no-owner --no-privileges` (or flag deviations that break cross-role restores).
   - Reject edits that rename/rewrite historical Alembic revision IDs in `migrations/versions/`, change `SAFETY_PREFIX = "pre-restore-"`, prune backups before `pg_restore` reads the target file, or break `seed()` idempotency.
   - Require tests in `tests/test_web.py` (or dedicated backup test modules) when schema, migrations, or backup/restore behavior changes.

4. **Synchronized dependency pins & CI/Docker security:**
   - Check that exact `==` pins stay identical across `pyproject.toml`, `requirements.txt`, `Dockerfile`, and `.github/workflows/ci.yml`.
   - Require 40-character SHA commit hashes on GitHub Actions `uses:`, minimal job `permissions:`, `persist-credentials: false`, no `sudo`, no hardcoded passwords, and no untrusted `${{ ... }}` interpolation inside `run:` blocks.

5. **Code quality & security:**
   - Enforce Ruff rules (`E`, `F`, `I`, `UP`, `B`, line length <= 110, Python 3.12+ syntax), PEP 257 docstrings, LF line endings (especially `.sh` and workflow files), `secrets.compare_digest` for secrets/CSRF, and parameterized SQL (`psycopg.sql`).
