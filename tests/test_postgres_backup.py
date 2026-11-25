"""Real PostgreSQL 16 integration tests for cross-version imovel <-> imobi backup/restore."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess  # nosec B404 - invokes pg_dump/pg_restore/sh in CI integration test
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import select

from imobi.config import Settings
from imobi.db import ensure_schema, session_scope
from imobi.models import Criteria, Listing, ListingDetail, PriceHistory, Source, Triage, TriageEvent
from imobi.seed import seed
from imobi.server import create_app

ADMIN_DSN = os.environ.get("TEST_POSTGRES_ADMIN_DSN", "")
pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="TEST_POSTGRES_ADMIN_DSN not set")

BACKUP_SH = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"


def _pg_bin(name: str) -> str:
    return shutil.which(name) or name


def _pg_url(role: str, password: str, db_name: str, *, driver: str = "postgresql+psycopg") -> str:
    parsed = urlparse(ADMIN_DSN)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    return f"{driver}://{role}:{password}@{host}:{port}/{db_name}"


def _pg_env(role: str, password: str, db_name: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    parsed = urlparse(ADMIN_DSN)
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": parsed.hostname or "127.0.0.1",
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": role,
            "PGPASSWORD": password,
            "PGDATABASE": db_name,
        }
    )
    if extra:
        env.update(extra)
    return env


def _drop_db_and_role(db_name: str, role: str) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _create_role_and_db(role: str, db_name: str, password: str) -> None:
    _drop_db_and_role(db_name, role)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(db_name), sql.Identifier(role)
            )
        )


def _run_cmd(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed binary invocations in integration test
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_legacy_data(settings: Settings) -> None:
    ensure_schema(settings)
    seed(settings)
    with session_scope(settings) as session:
        if session.get(Source, "chavesnamao") is None:
            session.add(
                Source(
                    id="chavesnamao",
                    name="Chaves na Mão",
                    type="json_ld",
                    config={},
                    enabled=True,
                )
            )
        criteria = session.get(Criteria, 1)
        assert criteria is not None
        criteria.max_price = Decimal("550000")

        listing = Listing(
            source_id="chavesnamao",
            external_id="legacy-pg-1",
            url="https://example.com/imovel/legacy-pg-1",
            title="Casa legado Postgres",
            property_type="house",
            neighborhood="Centro",
            price=Decimal("420000"),
            area_usable=Decimal("130"),
            is_active=True,
        )
        session.add(listing)
        session.flush()

        session.add(PriceHistory(listing_id=listing.id, price=Decimal("420000")))
        session.add(
            ListingDetail(
                listing_id=listing.id,
                title="Detalhe legado",
                description="Descrição preservada",
                photos=["https://cdn.chavesnamao.com.br/legacy.jpg"],
            )
        )
        session.add(
            Triage(
                listing_id=listing.id,
                seen=True,
                interested="yes",
                notes="visitar sabado",
            )
        )
        session.add(TriageEvent(listing_id=listing.id, decision="yes"))


def _assert_restored_data(settings: Settings) -> None:
    with session_scope(settings) as session:
        listing = session.scalar(select(Listing).where(Listing.external_id == "legacy-pg-1"))
        assert listing is not None
        assert listing.price == Decimal("420000")
        assert listing.triage is not None
        assert listing.triage.seen is True
        assert listing.triage.interested == "yes"
        assert listing.triage.notes == "visitar sabado"
        assert listing.detail is not None
        assert listing.detail.description == "Descrição preservada"
        assert len(listing.price_history) == 1
        criteria = session.get(Criteria, 1)
        assert criteria is not None
        assert criteria.max_price == Decimal("550000")


def _login_and_csrf(client: TestClient, password: str) -> str:
    login = client.post(
        "/login",
        data={"username": "admin", "password": password},
        follow_redirects=False,
    )
    assert login.status_code == 303
    page = client.get("/backup")
    assert page.status_code == 200
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def _dump_legacy_archives(tmp_path: Path, legacy_pass: str) -> tuple[Path, Path]:
    web_dump = tmp_path / "imovel-20261123-120000.dump"
    cli_dump = tmp_path / "imovel-cli-20261123-120000.dump"
    env = _pg_env("imovel", legacy_pass, "imovel")

    res_web = _run_cmd(
        [_pg_bin("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--file", str(web_dump)],
        env,
    )
    assert res_web.returncode == 0, res_web.stderr

    # Simulate v0.2.0 scripts/backup.sh (omitted --no-privileges).
    res_cli = _run_cmd(
        [_pg_bin("pg_dump"), "--format=custom", "--no-owner", "--file", str(cli_dump)],
        env,
    )
    assert res_cli.returncode == 0, res_cli.stderr
    return web_dump, cli_dump


def test_postgres_cross_version_backup_and_restore(tmp_path: Path) -> None:
    legacy_pass = secrets.token_hex(12)
    imobi_pass = secrets.token_hex(12)
    admin_pass = secrets.token_hex(12)

    try:
        _create_role_and_db("imovel", "imovel", legacy_pass)
        legacy_settings = Settings(
            _env_file=None,
            database_url=_pg_url("imovel", legacy_pass, "imovel"),
            backup_dir=str(tmp_path / "legacy-backups"),
            state_dir=str(tmp_path / "legacy-state"),
        )
        _seed_legacy_data(legacy_settings)
        web_dump, cli_dump = _dump_legacy_archives(tmp_path, legacy_pass)

        # Remove the legacy database and role completely before booting imobi.
        _drop_db_and_role("imovel", "imovel")

        _create_role_and_db("imobi", "imobi", imobi_pass)
        imobi_settings = Settings(
            _env_file=None,
            database_url=_pg_url("imobi", imobi_pass, "imobi"),
            admin_user="admin",
            admin_password=admin_pass,
            admin_secret_key=secrets.token_hex(16),
            backup_dir=str(tmp_path / "imobi-backups"),
            state_dir=str(tmp_path / "imobi-state"),
        )

        with TestClient(create_app(imobi_settings)) as client:
            csrf = _login_and_csrf(client, admin_pass)
            restore_res = client.post(
                "/backup/restore",
                data={"csrf": csrf, "password": admin_pass, "confirmation": "RESTAURAR"},
                files={"file": (web_dump.name, web_dump.read_bytes(), "application/octet-stream")},
                follow_redirects=False,
            )
            assert restore_res.status_code == 303

            _assert_restored_data(imobi_settings)
            seed(imobi_settings)
            _assert_restored_data(imobi_settings)

            # Delete the listing and test CLI restore of a v0.2.0 scripts/backup.sh dump.
            with session_scope(imobi_settings) as session:
                row = session.scalar(select(Listing).where(Listing.external_id == "legacy-pg-1"))
                assert row is not None
                session.delete(row)

            cli_env = _pg_env(
                "imobi",
                imobi_pass,
                "imobi",
                extra={"BACKUP_DIR": str(tmp_path / "cli-backups")},
            )
            cli_restore = _run_cmd(
                [_pg_bin("sh"), str(BACKUP_SH), "restore", str(cli_dump), "--yes"],
                cli_env,
            )
            assert cli_restore.returncode == 0, cli_restore.stderr
            _assert_restored_data(imobi_settings)

            # Reverse direction: imobi dump restored into a fresh v0.2.0 imovel DB.
            download_csrf = _login_and_csrf(client, admin_pass)
            downloaded = client.post("/backup/download", data={"csrf": download_csrf})
            assert downloaded.status_code == 200
            imobi_dump = tmp_path / "imobi-export.dump"
            imobi_dump.write_bytes(downloaded.content)

        _drop_db_and_role("imobi", "imobi")
        _create_role_and_db("imovel", "imovel", legacy_pass)
        reverse_restore = _run_cmd(
            [
                _pg_bin("pg_restore"),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--single-transaction",
                "--dbname",
                "imovel",
                str(imobi_dump),
            ],
            _pg_env("imovel", legacy_pass, "imovel"),
        )
        assert reverse_restore.returncode == 0, reverse_restore.stderr
        ensure_schema(legacy_settings)
        _assert_restored_data(legacy_settings)
    finally:
        _drop_db_and_role("imobi", "imobi")
        _drop_db_and_role("imovel", "imovel")
