"""Backup module: URL parsing, listing, rotation and restore guards (no database needed)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from imovel.backup import (
    BACKUP_PREFIX,
    BACKUP_SUFFIX,
    BackupError,
    _postgres_env,
    list_backups,
    prune,
    restore_backup,
)
from imovel.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite:///" + str(tmp_path / "db.sqlite"),
        backup_dir=str(tmp_path / "backups"),
    )


def _fake_backup(directory: Path, when: datetime, size: int = 1024) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{BACKUP_PREFIX}{when:%Y%m%d-%H%M%S}{BACKUP_SUFFIX}"
    path.write_bytes(b"x" * size)
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_postgres_env_parses_url():
    env = _postgres_env("postgresql+psycopg://user:p%40ss@db.example.com:6543/imovel")

    assert env["PGHOST"] == "db.example.com"
    assert env["PGPORT"] == "6543"
    assert env["PGUSER"] == "user"
    assert env["PGPASSWORD"] == "p@ss"
    assert env["PGDATABASE"] == "imovel"


def test_postgres_env_rejects_other_schemes():
    with pytest.raises(BackupError):
        _postgres_env("mysql://x/y")


def test_prune_keeps_the_newest(tmp_path):
    now = datetime.now(UTC)
    for days in range(5):
        _fake_backup(tmp_path, now - timedelta(days=days))

    removed = prune(tmp_path, keep=2)

    assert len(removed) == 3
    remaining = list_backups(tmp_path)
    assert len(remaining) == 2
    assert remaining[0].created_at > remaining[1].created_at


def test_prune_with_zero_keeps_everything(tmp_path):
    _fake_backup(tmp_path, datetime.now(UTC))
    assert prune(tmp_path, keep=0) == []


def test_list_backups_is_newest_first(tmp_path):
    now = datetime.now(UTC)
    _fake_backup(tmp_path, now - timedelta(hours=2))
    _fake_backup(tmp_path, now)

    items = list_backups(tmp_path)

    assert len(items) == 2
    assert items[0].created_at > items[1].created_at
    assert items[0].path.name.startswith(BACKUP_PREFIX)


def test_list_backups_ignores_other_files(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    _fake_backup(tmp_path, datetime.now(UTC))

    assert len(list_backups(tmp_path)) == 1


def test_restore_requires_confirmation(tmp_path):
    settings = _settings(tmp_path)
    backup = _fake_backup(tmp_path / "backups", datetime.now(UTC))

    with pytest.raises(BackupError, match="confirm"):
        restore_backup(settings, backup)


def test_restore_missing_file(tmp_path):
    settings = _settings(tmp_path)

    with pytest.raises(BackupError, match="not found"):
        restore_backup(settings, tmp_path / "nope.dump", assume_yes=True)


def test_sqlite_restore_roundtrip(tmp_path):
    """SQLite path: backup copies the file and restore puts it back, taking a safety copy."""
    database = tmp_path / "db.sqlite"
    database.write_bytes(b"version-1")
    settings = _settings(tmp_path)

    from imovel.backup import create_backup

    backup = create_backup(settings, keep=3)
    assert backup.path.exists()

    database.write_bytes(b"version-2")
    safety = restore_backup(settings, backup.path, assume_yes=True)

    assert database.read_bytes() == b"version-1"  # restored
    assert safety.path.exists()  # safety copy of version-2
