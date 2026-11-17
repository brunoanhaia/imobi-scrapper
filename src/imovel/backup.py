"""Database backup and restore.

Backups are PostgreSQL custom-format dumps (``pg_dump -Fc``) written to a mounted directory with
a timestamped name, pruned to the last N. Restoring always takes a safety dump first and requires
an explicit confirmation, because it replaces the live data.

SQLite (used for local smoke tests) is handled by copying the file.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import Settings

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "imovel-"
BACKUP_SUFFIX = ".dump"


class BackupError(RuntimeError):
    pass


@dataclass
class BackupFile:
    path: Path
    size: int
    created_at: datetime

    @property
    def size_mb(self) -> float:
        return round(self.size / (1024 * 1024), 2)


def _postgres_env(database_url: str) -> dict[str, str]:
    """Turn the SQLAlchemy URL into libpq environment variables."""
    parts = urlsplit(database_url.replace("postgresql+psycopg://", "postgresql://"))
    if parts.scheme not in {"postgresql", "postgres"}:
        raise BackupError(f"not a postgres URL: {database_url!r}")
    env = {
        "PGHOST": parts.hostname or "localhost",
        "PGPORT": str(parts.port or 5432),
        "PGUSER": unquote(parts.username or ""),
        "PGPASSWORD": unquote(parts.password or ""),
        "PGDATABASE": (parts.path or "/").lstrip("/"),
        "PGCONNECT_TIMEOUT": "10",
    }
    return {key: value for key, value in env.items() if value}


def _run(command: list[str], env: dict[str, str]) -> None:
    logger.debug("running %s", " ".join(command))
    result = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(
            f"{command[0]} failed ({result.returncode}): {result.stderr.strip()[:500]}"
        )


def backup_dir(settings: Settings) -> Path:
    return Path(getattr(settings, "backup_dir", "backups"))


def create_backup(
    settings: Settings,
    out_dir: Path | None = None,
    keep: int | None = None,
) -> BackupFile:
    """Dump the database and prune old files. Returns the new backup."""
    target_dir = out_dir or backup_dir(settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    path = target_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"

    if settings.database_url.startswith("sqlite"):
        source = settings.database_url.split("sqlite:///", 1)[-1]
        if not Path(source).exists():
            raise BackupError(f"sqlite database not found: {source}")
        shutil.copy2(source, path)
    else:
        env = _postgres_env(settings.database_url)
        _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(path)], env)

    keep = keep if keep is not None else getattr(settings, "backup_keep", 7)
    prune(target_dir, keep)
    logger.info("backup written to %s", path)
    return BackupFile(path=path, size=path.stat().st_size, created_at=datetime.now(UTC))


def list_backups(out_dir: Path) -> list[BackupFile]:
    if not out_dir.is_dir():
        return []
    files = [
        BackupFile(
            path=path,
            size=path.stat().st_size,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )
        for path in out_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
    ]
    return sorted(files, key=lambda item: item.created_at, reverse=True)


def prune(out_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` backups."""
    if keep <= 0:
        return []
    backups = list_backups(out_dir)
    removed = []
    for backup in backups[keep:]:
        backup.path.unlink(missing_ok=True)
        removed.append(backup.path)
    return removed


def restore_backup(settings: Settings, path: Path, assume_yes: bool = False) -> BackupFile:
    """Replace the database with ``path``. Takes a safety dump first."""
    if not assume_yes:
        raise BackupError("restore replaces the live database; pass --yes to confirm")
    if not path.exists():
        raise BackupError(f"backup not found: {path}")

    safety = create_backup(settings)
    logger.info("safety backup before restore: %s", safety.path)

    if settings.database_url.startswith("sqlite"):
        target = settings.database_url.split("sqlite:///", 1)[-1]
        shutil.copy2(path, target)
        return safety

    env = _postgres_env(settings.database_url)
    _run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--single-transaction",
            "--dbname",
            env["PGDATABASE"],
            str(path),
        ],
        env,
    )
    return safety
