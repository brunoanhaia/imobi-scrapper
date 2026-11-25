"""Routes for the on-demand database backup page."""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from ..backup import BackupBusy, BackupError, BackupManager
from .common import ensure_auth, settings_of, templates

BACKUP_TEMPLATE = "backup.html"
CSRF_INVALID = "sessão expirada; recarregue a página"
BACKUP_DISABLED = "backup desabilitado"
NOT_FOUND = "backup não encontrado"


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf") or ""
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=400, detail=CSRF_INVALID)


def _context(request: Request, **extra: object) -> dict:
    manager: BackupManager = request.app.state.backup
    settings = settings_of(request)
    context: dict[str, object] = {
        "request": request,
        "csrf": _csrf_token(request),
        "safety": manager.safety_dumps(),
        "max_mb": settings.backup_max_upload_mb,
        "error": None,
        "ok": None,
    }
    context.update(extra)
    return context


def _error_page(request: Request, message: str) -> Response:
    return templates.TemplateResponse(
        request, BACKUP_TEMPLATE, _context(request, error=message), status_code=400
    )


def _save_upload(file: UploadFile, limit: int) -> Path:
    """Copy the uploaded archive to a temporary file, enforcing the size limit."""
    handle, name = tempfile.mkstemp(prefix="imobi-upload-", suffix=".dump")
    path = Path(name)
    total = 0
    completed = False
    try:
        with os.fdopen(handle, "wb") as destination:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise BackupError(f"arquivo maior que {limit // (1024 * 1024)} MB")
                destination.write(chunk)
        completed = True
    finally:
        if not completed:
            path.unlink(missing_ok=True)
    return path


router = APIRouter()


@router.get(
    "/backup",
    response_class=HTMLResponse,
    responses={404: {"description": BACKUP_DISABLED}},
)
def backup_page(request: Request) -> Response:
    ensure_auth(request)
    if not settings_of(request).backup_enabled:
        raise HTTPException(status_code=404, detail=BACKUP_DISABLED)
    ok = "Backup restaurado com sucesso." if request.query_params.get("ok") else None
    return templates.TemplateResponse(request, BACKUP_TEMPLATE, _context(request, ok=ok))


@router.post(
    "/backup/download",
    responses={400: {"description": CSRF_INVALID}, 404: {"description": BACKUP_DISABLED}},
)
def backup_download(request: Request, csrf: str = Form(...)) -> Response:
    ensure_auth(request)
    if not settings_of(request).backup_enabled:
        raise HTTPException(status_code=404, detail=BACKUP_DISABLED)
    _check_csrf(request, csrf)
    manager: BackupManager = request.app.state.backup
    try:
        path, filename = manager.create_download()
    except BackupBusy as error:
        return templates.TemplateResponse(
            request, BACKUP_TEMPLATE, _context(request, error=str(error)), status_code=409
        )
    except (BackupError, OSError) as error:
        return _error_page(request, str(error))
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post(
    "/backup/restore",
    response_class=HTMLResponse,
    responses={400: {"description": CSRF_INVALID}, 404: {"description": BACKUP_DISABLED}},
)
async def backup_restore(
    request: Request,
    file: Annotated[UploadFile, File()],
    password: str = Form(...),
    confirmation: str = Form(...),
    csrf: str = Form(...),
) -> Response:
    ensure_auth(request)
    settings = settings_of(request)
    if not settings.backup_enabled:
        raise HTTPException(status_code=404, detail=BACKUP_DISABLED)
    _check_csrf(request, csrf)
    if confirmation.strip().upper() != "RESTAURAR":
        return _error_page(request, "Digite RESTAURAR para confirmar a substituição dos dados.")
    if not secrets.compare_digest(password, settings.admin_password):
        return _error_page(request, "Senha do admin incorreta.")

    limit = settings.backup_max_upload_mb * 1024 * 1024
    if file.size is not None and file.size > limit:
        return _error_page(request, f"Arquivo maior que {settings.backup_max_upload_mb} MB.")
    try:
        path = await run_in_threadpool(_save_upload, file, limit)
    except OSError as error:
        return _error_page(request, f"não foi possível ler o arquivo: {error}")
    try:
        manager: BackupManager = request.app.state.backup
        manager.validate(path)
        await run_in_threadpool(manager.restore, path)
    except BackupError as error:
        return _error_page(request, str(error))
    finally:
        path.unlink(missing_ok=True)
    return RedirectResponse("/backup?ok=1", status_code=303)


@router.get("/backup/safety/{name}", responses={404: {"description": NOT_FOUND}})
def backup_safety(name: str, request: Request) -> Response:
    ensure_auth(request)
    if not settings_of(request).backup_enabled:
        raise HTTPException(status_code=404, detail=BACKUP_DISABLED)
    path = request.app.state.backup.safety_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
