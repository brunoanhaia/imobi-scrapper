"""On-demand database dump and restore used by the backup page."""
# A dump is produced on demand and streamed to the browser; a restore receives an uploaded
# archive, keeps a safety dump of the current database and replaces the live data in one step
# (``--single-transaction`` on Postgres). The manager also owns the maintenance flag shared
# with the scraper container so no collection runs while the schema is being replaced.

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404 - fixed pg_dump/pg_restore commands, no shell
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from .config import Settings
from .db import ensure_schema, init_engine

logger = logging.getLogger(__name__)

DUMP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.dump$")
SAFETY_PREFIX = "pre-restore-"
DOWNLOAD_PREFIX = "imovel-download-"
SQLITE_HEADER = b"SQLite format 3\x00"
SAFETY_KEEP = 3
DOWNLOAD_TTL_SECONDS = 3600
PG_DUMP = shutil.which("pg_dump") or "/usr/bin/pg_dump"
PG_RESTORE = shutil.which("pg_restore") or "/usr/bin/pg_restore"


class BackupError(RuntimeError):
    """A dump or restore could not be completed."""


class BackupBusy(BackupError):
    """Another dump or restore is already running."""


def human_size(size: int) -> str:
    """Render a byte count in a compact pt-BR friendly form."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class BackupManager:
    """Generate dumps, restore uploads and keep a few safety dumps per database."""

    def __init__(self, settings: Settings) -> None:
        """Prepare the backup directory and the maintenance flag for this database."""
        self.settings = settings
        self.directory = Path(settings.backup_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_dir = Path(settings.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.flag = self.state_dir / "maintenance"
        self.maintenance = threading.Event()
        if self.flag.exists():
            logger.warning("removing a stale maintenance flag from a previous run")
            self.flag.unlink(missing_ok=True)
        self._lock = threading.Lock()
        self.url = make_url(settings.database_url)
        self.is_sqlite = self.url.get_backend_name() == "sqlite"
        self._prune_downloads()

    # ------------------------------------------------------------------ public API

    def create_download(self) -> tuple[Path, str]:
        """Dump the database into a temporary file and return it with a friendly name."""
        with self._exclusive():
            handle, name = tempfile.mkstemp(
                prefix=DOWNLOAD_PREFIX, suffix=".dump", dir=self.directory
            )
            os.close(handle)
            path = Path(name)
            try:
                self.dump(path)
            except (BackupError, OSError, sqlite3.Error):
                path.unlink(missing_ok=True)
                raise
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return path, f"imovel-{stamp}.dump"

    def restore(self, uploaded: Path) -> Path:
        """Validate and restore ``uploaded``; returns the safety dump created before it."""
        with self._exclusive():
            safety = self._safety_path()
            self._enter_maintenance()
            try:
                self.dump(safety)
                self._prune_safety()
                self._apply(uploaded)
            finally:
                self._leave_maintenance()
        logger.info("database restored from an uploaded archive")
        return safety

    def validate(self, path: Path) -> None:
        """Reject files that are not a dump produced by this application's database engine."""
        if self.is_sqlite:
            with path.open("rb") as handle:
                header = handle.read(len(SQLITE_HEADER))
            if header != SQLITE_HEADER:
                raise BackupError("arquivo inválido: não é um banco SQLite")
            return
        try:
            result = subprocess.run(  # nosec B603 - fixed binary, controlled paths
                [PG_RESTORE, "--list", str(path)],
                env=self._pg_env(),
                capture_output=True,
                timeout=self.settings.backup_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise BackupError(f"não foi possível ler o arquivo: {error}") from error
        if result.returncode != 0:
            raise BackupError(f"arquivo inválido: {self._error_text(result)}")

    def safety_dumps(self) -> list[dict]:
        files = sorted(
            self.directory.glob(f"{SAFETY_PREFIX}*.dump"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "when": datetime.fromtimestamp(path.stat().st_mtime, UTC),
            }
            for path in files
        ]

    def safety_path(self, name: str) -> Path | None:
        """Resolve a safety dump name without allowing path traversal."""
        if not DUMP_NAME.match(name) or not name.startswith(SAFETY_PREFIX):
            return None
        path = (self.directory / name).resolve()
        if path.parent != self.directory.resolve() or not path.is_file():
            return None
        return path

    # ------------------------------------------------------------------ dump/restore

    def dump(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.is_sqlite:
            try:
                self._sqlite_copy(self._sqlite_path(), destination)
            except sqlite3.Error as error:
                raise BackupError(f"cópia do SQLite falhou: {error}") from error
            return
        try:
            with destination.open("wb") as handle:
                result = subprocess.run(  # nosec B603 - fixed binary, no shell
                    [PG_DUMP, "--format=custom", "--no-owner", "--no-privileges"],
                    env=self._pg_env(),
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    timeout=self.settings.backup_timeout_seconds,
                )
        except (OSError, subprocess.SubprocessError) as error:
            destination.unlink(missing_ok=True)
            raise BackupError(f"pg_dump falhou: {error}") from error
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise BackupError(f"pg_dump falhou: {self._error_text(result)}")

    def _apply(self, uploaded: Path) -> None:
        engine = init_engine(self.settings)
        engine.dispose()
        if self.is_sqlite:
            try:
                self._restore_sqlite(uploaded)
            except (OSError, sqlite3.Error) as error:
                raise BackupError(f"restauração do SQLite falhou: {error}") from error
        else:
            self._terminate_connections()
            try:
                result = subprocess.run(  # nosec B603 - fixed binary, no shell
                    [
                        PG_RESTORE,
                        "--clean",
                        "--if-exists",
                        "--no-owner",
                        "--no-privileges",
                        "--single-transaction",
                        "--dbname",
                        self.url.database or "",
                        str(uploaded),
                    ],
                    env=self._pg_env(),
                    capture_output=True,
                    timeout=self.settings.backup_timeout_seconds,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise BackupError(f"pg_restore falhou: {error}") from error
            if result.returncode != 0:
                raise BackupError(f"pg_restore falhou: {self._error_text(result)}")
        engine.dispose()
        # An older archive may predate a migration; bring the restored schema up to head.
        ensure_schema(self.settings)

    def _restore_sqlite(self, uploaded: Path) -> None:
        target = self._sqlite_path()
        temporary = target.with_name(f"{target.name}.restore")
        try:
            self._sqlite_copy(uploaded, temporary)
            os.replace(temporary, target)
        except (OSError, sqlite3.Error):
            temporary.unlink(missing_ok=True)
            raise

    def _sqlite_path(self) -> Path:
        database = self.url.database or ""
        if not database or database == ":memory:":
            raise BackupError("o banco SQLite em memória não pode ser copiado")
        return Path(database)

    @staticmethod
    def _sqlite_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        origin = sqlite3.connect(str(source))
        target = sqlite3.connect(str(destination))
        try:
            origin.backup(target)
        finally:
            target.close()
            origin.close()

    def _terminate_connections(self) -> None:
        dsn = self.url.set(drivername="postgresql").render_as_string(hide_password=False)
        try:
            with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as connection:
                connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
        except psycopg.Error as error:  # best effort: pg_restore reports the real problem
            logger.warning("could not terminate other database sessions: %s", error)

    def _pg_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PGHOST": self.url.host or "localhost",
                "PGPORT": str(self.url.port or 5432),
                "PGUSER": self.url.username or "",
                "PGPASSWORD": self.url.password or "",
                "PGDATABASE": self.url.database or "",
                "PGCONNECT_TIMEOUT": "10",
            }
        )
        return env

    @staticmethod
    def _error_text(result: subprocess.CompletedProcess) -> str:
        text = (result.stderr or b"").decode("utf-8", "replace").strip()
        return text[-400:] or f"código {result.returncode}"

    # ------------------------------------------------------------------ maintenance

    def _enter_maintenance(self) -> None:
        self.flag.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        self.maintenance.set()

    def _leave_maintenance(self) -> None:
        self.maintenance.clear()
        self.flag.unlink(missing_ok=True)

    # ------------------------------------------------------------------ files

    def _safety_path(self) -> Path:
        base = f"{SAFETY_PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        path = self.directory / f"{base}.dump"
        counter = 1
        while path.exists():
            path = self.directory / f"{base}-{counter}.dump"
            counter += 1
        return path

    def _prune_safety(self) -> None:
        files = sorted(
            self.directory.glob(f"{SAFETY_PREFIX}*.dump"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in files[SAFETY_KEEP:]:
            old.unlink(missing_ok=True)

    def _prune_downloads(self) -> None:
        cutoff = datetime.now().timestamp() - DOWNLOAD_TTL_SECONDS
        for stale in self.directory.glob(f"{DOWNLOAD_PREFIX}*.dump"):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink(missing_ok=True)
            except OSError:
                continue

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise BackupBusy("outro backup ou restauração já está em andamento")
        try:
            yield
        finally:
            self._lock.release()
