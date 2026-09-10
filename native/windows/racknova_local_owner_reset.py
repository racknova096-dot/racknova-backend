from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlmodel import Session

import multiempresa_tenant as rn_tenant
from racknova_native_config import backups_dir, config_path, load_config, load_secrets, secrets_path


RESET_CONFIRMATION = "BORRAR RACKNOVA LOCAL"

# Estas tablas mantienen la identidad de la instalación, el acceso del dueño
# y la posición del nodo dentro de RackNova Cloud. Todo lo demás es dato
# operativo y puede ser eliminado por el reset propietario.
PRESERVED_TABLES = {
    "empresas",
    "empresa_usuarios",
    "usuario",
    "racknova_platform_admins",
    "racknova_nodos",
    "racknova_sync_cursor",
    "racknova_sync_pos_cursor",
}


class LocalOwnerResetIn(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    confirmation: str = Field(min_length=1, max_length=80)


def _current_role(session: Session, current_user: Any) -> str:
    config = load_config()
    empresa_id = str(config.get("empresa_id") or "").strip() or None
    membership = rn_tenant.bind_empresa(
        session,
        current_user,
        requested_empresa_id=empresa_id,
    )
    return str(membership.get("rol") or "viewer").strip().lower()


def _is_owner(session: Session, current_user: Any) -> bool:
    if rn_tenant.is_platform_superadmin(session, current_user):
        return True
    return _current_role(session, current_user) == "owner"


def _require_owner(session: Session, current_user: Any) -> None:
    if not _is_owner(session, current_user):
        raise HTTPException(
            status_code=403,
            detail="Esta acción es exclusiva del propietario de RackNova.",
        )


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _pg_dump_path() -> Path:
    candidate = _install_root() / "PostgreSQL" / "bin" / "pg_dump.exe"
    if candidate.exists():
        return candidate

    found = shutil.which("pg_dump")
    if found:
        return Path(found)

    raise RuntimeError("No encontré pg_dump para crear el respaldo previo.")


def _create_backup() -> Path:
    config = load_config()
    secrets = load_secrets()

    password = str(secrets.get("db_password") or "")
    if not password:
        raise RuntimeError("No existe la contraseña local de PostgreSQL protegida.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = backups_dir() / f"OwnerReset-{stamp}"
    root.mkdir(parents=True, exist_ok=False)

    cfg = config_path()
    sec = secrets_path()
    if cfg.exists():
        shutil.copy2(cfg, root / "config.json")
    if sec.exists():
        shutil.copy2(sec, root / "secrets.dat")

    dump_file = root / "racknova-before-owner-reset.dump"
    host = str(config.get("db_host") or "127.0.0.1")
    port = str(int(config.get("db_port") or 54329))
    database = str(config.get("db_name") or "racknova")
    user = str(config.get("db_user") or "racknova_app")

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    result = subprocess.run(
        [
            str(_pg_dump_path()),
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            database,
            "-Fc",
            "-f",
            str(dump_file),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if result.returncode != 0 or not dump_file.exists() or dump_file.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "No se pudo crear el respaldo previo al reset."
            + (f" Detalle: {detail[-1000:]}" if detail else "")
        )

    (root / "README.txt").write_text(
        "RackNova Local - respaldo previo a Restablecer datos locales\n"
        f"Fecha: {datetime.now().isoformat(timespec='seconds')}\n"
        "Este respaldo se creó automáticamente antes de borrar datos operativos.\n"
        "Se preservaron config.json y secrets.dat protegidos de la instalación.\n",
        encoding="utf-8",
    )

    return root


def _business_tables(session: Session) -> list[str]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname='public'
            ORDER BY tablename
            """
        )
    ).scalars().all()

    tables: list[str] = []
    for raw in rows:
        table = str(raw)
        if table in PRESERVED_TABLES:
            continue
        # La tabla de migraciones/esquema no es información de negocio.
        if table in {"alembic_version", "schema_migrations"}:
            continue
        if not table.replace("_", "").isalnum():
            raise RuntimeError(f"Nombre de tabla local no permitido: {table!r}")
        tables.append(table)
    return tables


def _truncate_operational_data(session: Session) -> list[str]:
    tables = _business_tables(session)
    if not tables:
        return []

    quoted = ", ".join(f'"{table}"' for table in tables)
    session.connection().execute(
        sa_text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
    )
    session.commit()
    return tables


def register_native_owner_reset_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
    verify_password: Callable[[str, str], bool],
) -> None:
    @app.get(
        "/racknova-native/owner-reset/capability",
        include_in_schema=False,
    )
    def local_owner_reset_capability(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
    ) -> dict[str, Any]:
        allowed = _is_owner(session, current_user)
        return {
            "native": True,
            "allowed": allowed,
            "confirmation": RESET_CONFIRMATION if allowed else None,
        }

    @app.post(
        "/racknova-native/owner-reset",
        include_in_schema=False,
    )
    def local_owner_reset(
        payload: LocalOwnerResetIn,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
    ) -> dict[str, Any]:
        _require_owner(session, current_user)

        if payload.confirmation.strip().upper() != RESET_CONFIRMATION:
            raise HTTPException(
                status_code=400,
                detail=f'Escribe exactamente "{RESET_CONFIRMATION}" para confirmar.',
            )

        password_hash = str(getattr(current_user, "password_hash", "") or "")
        if not password_hash or not verify_password(payload.password, password_hash):
            raise HTTPException(
                status_code=403,
                detail="La contraseña del propietario no es correcta.",
            )

        try:
            backup = _create_backup()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "No se borró ningún dato porque falló el respaldo obligatorio: "
                    + str(exc)
                ),
            ) from exc

        try:
            tables = _truncate_operational_data(session)
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    "El respaldo se creó, pero no fue posible limpiar la base local: "
                    + str(exc)
                ),
            ) from exc

        return {
            "ok": True,
            "message": "RackNova Local quedó limpio.",
            "tables_cleared": tables,
            "backup": str(backup),
        }
