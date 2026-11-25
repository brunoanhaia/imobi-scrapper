"""Offline POSIX sh contract tests for scripts/backup.sh."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - invokes repo script with controlled fake binaries
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")

BACKUP_SH = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"


def _install_fake_pg_tools(bin_dir: Path, log_file: Path, restored_out: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)

    pg_dump = bin_dir / "pg_dump"
    pg_dump.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f'printf "pg_dump %s\\n" "$*" >> "{log_file}"\n'
        'target=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '    if [ "$1" = "--file" ]; then\n'
        '        target="$2"\n'
        "        shift 2\n"
        "        continue\n"
        "    fi\n"
        "    shift\n"
        "done\n"
        'printf "safety-dump" > "$target"\n',
        encoding="utf-8",
        newline="\n",
    )
    pg_dump.chmod(0o700)

    pg_restore = bin_dir / "pg_restore"
    pg_restore.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f'printf "pg_restore db=%s args=%s\\n" "$PGDATABASE" "$*" >> "{log_file}"\n'
        'file=""\n'
        'for arg in "$@"; do\n'
        '    file="$arg"\n'
        "done\n"
        '[ -f "$file" ] || exit 99\n'
        f'cp "$file" "{restored_out}"\n',
        encoding="utf-8",
        newline="\n",
    )
    pg_restore.chmod(0o700)


def test_backup_sh_restores_legacy_dump_without_pruning_it(tmp_path: Path) -> None:
    sh_path = shutil.which("sh")
    assert sh_path is not None

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    for idx in range(1, 8):
        path = backups_dir / f"imobi-20261124-00000{idx}.dump"
        path.write_bytes(b"newer")
        mtime = 1_700_010_000 + idx
        os.utime(path, (mtime, mtime))

    legacy_dump = backups_dir / "imovel-20261120-120000.dump"
    legacy_dump.write_bytes(b"legacy-v020-archive")
    os.utime(legacy_dump, (1_700_000_000, 1_700_000_000))

    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "calls.log"
    restored_out = tmp_path / "restored.dump"
    _install_fake_pg_tools(bin_dir, log_file, restored_out)

    env = os.environ.copy()
    env.update(
        {
            "BACKUP_DIR": str(backups_dir),
            "BACKUP_KEEP": "7",
            "PGDATABASE": "imobi",
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        }
    )

    result = subprocess.run(  # nosec B603 - fixed script path and args
        [sh_path, str(BACKUP_SH), "restore", str(legacy_dump), "--yes"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert legacy_dump.exists()
    assert restored_out.read_bytes() == b"legacy-v020-archive"

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert "--no-owner --no-privileges" in lines[0]
    assert (
        "--clean --if-exists --no-owner --no-privileges --single-transaction --dbname imobi"
        in lines[1]
    )


def test_backup_sh_list_and_prune_handle_both_prefixes(tmp_path: Path) -> None:
    sh_path = shutil.which("sh")
    assert sh_path is not None

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    legacy_dump = backups_dir / "imovel-20261120-120000.dump"
    legacy_dump.write_bytes(b"legacy")
    os.utime(legacy_dump, (1_700_000_000, 1_700_000_000))

    current_dump = backups_dir / "imobi-20261124-120000.dump"
    current_dump.write_bytes(b"current")
    os.utime(current_dump, (1_700_010_000, 1_700_010_000))

    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "calls.log"
    restored_out = tmp_path / "restored.dump"
    _install_fake_pg_tools(bin_dir, log_file, restored_out)

    env = os.environ.copy()
    env.update(
        {
            "BACKUP_DIR": str(backups_dir),
            "BACKUP_KEEP": "2",
            "PGDATABASE": "imobi",
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        }
    )

    listed = subprocess.run(  # nosec B603 - fixed script path and args
        [sh_path, str(BACKUP_SH), "list"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.index("imobi-20261124-120000.dump") < listed.stdout.index(
        "imovel-20261120-120000.dump"
    )

    backed_up = subprocess.run(  # nosec B603 - fixed script path and args
        [sh_path, str(BACKUP_SH), "backup"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert backed_up.returncode == 0, backed_up.stderr
    assert not legacy_dump.exists()
    assert current_dump.exists()
    assert len(list(backups_dir.glob("imobi-*.dump"))) == 2
