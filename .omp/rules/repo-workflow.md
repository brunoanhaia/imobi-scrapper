# Repository Workflow Rules (`imobi-scrapper`)

Follow the full specification in [`AGENTS.md`](../../AGENTS.md):

- **One branch per feature/fix:** Run `git fetch origin` and branch from `origin/main` (`feat/...`, `fix/...`, `chore/...`, `docs/...`), unless continuing an approved follow-up on the active PR branch. Avoid bundling unrelated features into an existing branch or committing directly to `main`.
- **Conventional Commits:** `release-please` controls SemVer and `CHANGELOG.md`.
- **Naming:** Keep package, CLI (`imovel`, or `imobi` if migrated), Docker, CI, and Compose names consistent while preserving `/imovel/{listing_id}` routes and third-party names (`Imovelweb`, `Imoview`).
- **Backups & migrations:** Keep `.dump` restore cross-version compatible (`--no-owner --no-privileges`, untouched Alembic revision IDs, safe restore ordering in `scripts/backup.sh`).
- **Dependency pins:** Keep exact `==` versions synchronized across `pyproject.toml`, `requirements.txt`, `Dockerfile`, and `.github/workflows/ci.yml` (`--only-binary :all:`).
- **Quality & security:** Follow Ruff (`<= 110` cols, `E, F, I, UP, B`), PEP 257 docstrings, LF line endings, SHA-pinned GitHub Actions, and offline `pytest -q` verification.
