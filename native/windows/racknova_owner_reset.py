from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import Session


CONFIRMATION_TEXT = "BORRAR RACKNOVA LOCAL"

# Estas tablas mantienen la instalación utilizable después de limpiar los datos
# comerciales. Los cursores se conservan para evitar reejecutar historia antigua
# de Cloud después de un reset manual.
PRESERVED_TABLES = {
    "alembic_version",
    "empresas",
    "empresa_usuarios",
    "usuario",
    "racknova_platform_admins",
    "racknova_nodos",
    "racknova_sync_cursor",
    "racknova_sync_pos_cursor",
}


class OwnerResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirmation: str = Field(min_length=1, max_length=80)


def _program_data() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "RackNova"


def _install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _primary_admin(session: Session) -> tuple[int, str] | None:
    row = session.connection().execute(
        text(
            """
            SELECT id_usuario, usuario
            FROM usuario
            WHERE activo = TRUE
              AND LOWER(COALESCE(rol, '')) = 'admin'
            ORDER BY fecha_creacion ASC NULLS LAST, id_usuario ASC
            LIMIT 1
            """
        )
    ).first()
    if not row:
        return None
    return int(row[0]), str(row[1])


def _require_owner(session: Session, current_user: Any) -> tuple[int, str]:
    if str(getattr(current_user, "rol", "") or "").lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta función está reservada al propietario de RackNova.",
        )

    owner = _primary_admin(session)
    if owner is None or int(getattr(current_user, "id_usuario", -1) or -1) != owner[0]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta no es el propietario principal de esta instalación.",
        )
    return owner


def _backup_database(
    *,
    config: dict[str, Any],
    secrets: dict[str, Any],
    actor: str,
) -> Path:
    root = _program_data()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / "Backups" / f"OwnerReset-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    # Conservamos también la configuración y secrets.dat cifrado con DPAPI.
    config_dir = root / "Config"
    for name in ("config.json", "secrets.dat"):
        source = config_dir / name
        if source.exists():
            shutil.copy2(source, backup_dir / name)

    pg_dump = _install_dir() / "PostgreSQL" / "bin" / "pg_dump.exe"
    if not pg_dump.exists():
        candidate = shutil.which("pg_dump")
        if candidate:
            pg_dump = Path(candidate)
    if not pg_dump.exists():
        raise RuntimeError("No se encontró pg_dump; el borrado fue cancelado.")

    host = str(config.get("db_host") or "127.0.0.1")
    port = str(int(config.get("db_port") or 54329))
    db_name = str(config.get("db_name") or "racknova")
    super_user = str(config.get("pg_super_user") or "racknova_super")
    super_password = str(secrets.get("pg_super_password") or "").strip()
    if not super_password:
        raise RuntimeError(
            "No está disponible la credencial de respaldo PostgreSQL; el borrado fue cancelado."
        )

    dump_path = backup_dir / "racknova-before-reset.dump"
    env = os.environ.copy()
    env["PGPASSWORD"] = super_password

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    result = subprocess.run(
        [
            str(pg_dump),
            "-h",
            host,
            "-p",
            port,
            "-U",
            super_user,
            "-d",
            db_name,
            "-Fc",
            "-f",
            str(dump_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=creationflags,
        check=False,
    )

    if result.returncode != 0 or not dump_path.exists() or dump_path.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "Falló el respaldo PostgreSQL; el borrado fue cancelado. "
            + detail[-1200:]
        )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "database": db_name,
        "reason": "owner_local_database_reset",
        "preserved_tables": sorted(PRESERVED_TABLES),
        "dump_file": dump_path.name,
        "dump_bytes": dump_path.stat().st_size,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_dir


def _commercial_tables(session: Session) -> list[str]:
    rows = session.connection().execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public'
              AND table_type='BASE TABLE'
            ORDER BY table_name
            """
        )
    ).scalars().all()

    return [
        str(table)
        for table in rows
        if str(table) not in PRESERVED_TABLES
    ]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _reset_local_data(session: Session) -> dict[str, Any]:
    tables = _commercial_tables(session)
    row_counts: dict[str, int] = {}

    for table in tables:
        qtable = _quote_identifier(table)
        try:
            count = session.connection().execute(
                text(f"SELECT COUNT(*) FROM {qtable}")
            ).scalar_one()
            row_counts[table] = int(count or 0)
        except Exception:
            row_counts[table] = -1

    if tables:
        quoted = ", ".join(_quote_identifier(table) for table in tables)
        # TRUNCATE no pasa por la captura de eventos de aplicación y, por tanto,
        # no genera órdenes DELETE que puedan propagarse accidentalmente a Cloud.
        session.connection().exec_driver_sql(
            f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"
        )

    session.commit()

    return {
        "tables_cleared": len(tables),
        "rows_removed": sum(value for value in row_counts.values() if value > 0),
        "details": row_counts,
    }


def register_owner_reset_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
    verify_password: Callable[[str, str], bool],
    config: dict[str, Any],
    secrets: dict[str, Any],
) -> None:
    @app.get(
        "/racknova-native/owner-reset/status",
        include_in_schema=False,
    )
    def owner_reset_status(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
    ) -> dict[str, Any]:
        owner = _primary_admin(session)
        is_owner = bool(
            owner
            and str(getattr(current_user, "rol", "") or "").lower() == "admin"
            and int(getattr(current_user, "id_usuario", -1) or -1) == owner[0]
        )
        return {
            "available": True,
            "runtime": "native_windows",
            "is_owner": is_owner,
            "confirmation_text": CONFIRMATION_TEXT if is_owner else None,
        }

    @app.post(
        "/racknova-native/owner-reset",
        include_in_schema=False,
    )
    def owner_reset(
        payload: OwnerResetRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
    ) -> dict[str, Any]:
        _require_owner(session, current_user)

        if payload.confirmation.strip() != CONFIRMATION_TEXT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Escribe exactamente "{CONFIRMATION_TEXT}" para confirmar.',
            )

        password_hash = str(getattr(current_user, "password_hash", "") or "")
        try:
            valid_password = bool(
                password_hash and verify_password(payload.password, password_hash)
            )
        except Exception:
            valid_password = False
        if not valid_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La contraseña del propietario no es correcta.",
            )

        actor = str(getattr(current_user, "usuario", "") or "owner")
        try:
            backup_dir = _backup_database(
                config=config,
                secrets=secrets,
                actor=actor,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc

        try:
            result = _reset_local_data(session)
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "El respaldo sí fue creado, pero no pude limpiar la base local. "
                    f"Respaldo: {backup_dir}. Error: {exc}"
                ),
            ) from exc

        return {
            "ok": True,
            "message": "Los datos operativos de RackNova Local fueron eliminados.",
            "backup": str(backup_dir),
            "identity_preserved": True,
            "cloud_link_preserved": True,
            **result,
        }
