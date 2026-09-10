from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psycopg2
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

import multiempresa_tenant as rn_tenant
from racknova_native_config import backups_dir


CONFIRMATION_PHRASE = "BORRAR DATOS LOCALES"

PRESERVED_TABLES = {
    "empresas",
    "empresa_usuarios",
    "usuario",
    "racknova_platform_admins",
    "racknova_nodos",
    "alembic_version",
}


class OwnerResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=300)
    confirmation: str = Field(min_length=1, max_length=120)


def _pg_dump_path() -> Path:
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        candidates.append(
            Path(sys.executable).resolve().parent
            / "PostgreSQL"
            / "bin"
            / "pg_dump.exe"
        )

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates.append(
        program_files / "RackNova" / "PostgreSQL" / "bin" / "pg_dump.exe"
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise RuntimeError("No encontré pg_dump.exe del PostgreSQL incluido en RackNova.")


def _database_settings(
    config: dict[str, Any],
    secrets: dict[str, Any],
) -> dict[str, Any]:
    password = str(secrets.get("pg_super_password") or "").strip()
    if not password:
        raise RuntimeError(
            "No existe la credencial local de PostgreSQL necesaria para el respaldo."
        )

    return {
        "host": str(config.get("db_host") or "127.0.0.1"),
        "port": int(config.get("db_port") or 54329),
        "dbname": str(config.get("db_name") or "racknova"),
        "user": "racknova_super",
        "password": password,
    }


def _create_backup(
    config: dict[str, Any],
    secrets: dict[str, Any],
    *,
    actor: str,
    empresa_id: str,
) -> Path:
    settings = _database_settings(config, secrets)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = backups_dir() / f"OwnerReset-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)

    dump_path = destination / "racknova-before-reset.dump"
    pg_dump = _pg_dump_path()

    env = os.environ.copy()
    env["PGPASSWORD"] = settings["password"]

    args = [
        str(pg_dump),
        "--host",
        settings["host"],
        "--port",
        str(settings["port"]),
        "--username",
        settings["user"],
        "--dbname",
        settings["dbname"],
        "--format",
        "custom",
        "--no-owner",
        "--file",
        str(dump_path),
    ]

    result = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if result.returncode != 0 or not dump_path.exists():
        diagnostic = {
            "created_at": datetime.now().isoformat(),
            "actor": actor,
            "empresa_id": empresa_id,
            "return_code": result.returncode,
            "stderr": (result.stderr or "")[-4000:],
        }
        (destination / "BACKUP_FAILED.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise RuntimeError(
            "El respaldo automático falló. No se borró ningún dato local."
        )

    metadata = {
        "created_at": datetime.now().isoformat(),
        "actor": actor,
        "empresa_id": empresa_id,
        "node_code": str(config.get("node_code") or ""),
        "database": settings["dbname"],
        "backup_file": dump_path.name,
        "purpose": "Respaldo automático previo al borrado de datos locales por propietario.",
    }
    (destination / "README.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return destination


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _reset_database(
    config: dict[str, Any],
    secrets: dict[str, Any],
) -> list[str]:
    settings = _database_settings(config, secrets)

    connection = psycopg2.connect(
        host=settings["host"],
        port=settings["port"],
        dbname=settings["dbname"],
        user=settings["user"],
        password=settings["password"],
        connect_timeout=10,
    )
    connection.autocommit = False

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            )
            all_tables = [str(row[0]) for row in cursor.fetchall()]
            targets = [
                table
                for table in all_tables
                if table not in PRESERVED_TABLES
            ]

            if not targets:
                connection.rollback()
                return []

            cursor.execute(
                """
                SELECT child.relname, parent.relname
                FROM pg_constraint c
                JOIN pg_class child ON child.oid = c.conrelid
                JOIN pg_class parent ON parent.oid = c.confrelid
                JOIN pg_namespace nchild ON nchild.oid = child.relnamespace
                JOIN pg_namespace nparent ON nparent.oid = parent.relnamespace
                WHERE c.contype = 'f'
                  AND nchild.nspname = 'public'
                  AND nparent.nspname = 'public'
                  AND child.relname = ANY(%s)
                  AND parent.relname = ANY(%s)
                """,
                (list(PRESERVED_TABLES), targets),
            )
            unsafe_dependencies = cursor.fetchall()
            if unsafe_dependencies:
                pairs = ", ".join(
                    f"{child}->{parent}"
                    for child, parent in unsafe_dependencies
                )
                raise RuntimeError(
                    "El borrado se detuvo porque una tabla protegida depende de "
                    f"datos operativos: {pairs}"
                )

            target_sql = ", ".join(_quote_identifier(name) for name in targets)
            cursor.execute(
                f"TRUNCATE TABLE {target_sql} RESTART IDENTITY CASCADE"
            )

        connection.commit()
        return targets
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
        rn_empresa_id: str | None = Header(
            default=None,
            alias="X-Empresa-ID",
        ),
    ) -> dict[str, Any]:
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner"},
        )
        return {
            "ok": True,
            "available": True,
            "runtime": "native_windows",
            "role": str(selected.get("rol") or "").lower(),
            "confirmation_phrase": CONFIRMATION_PHRASE,
            "preserves": [
                "usuarios",
                "empresa",
                "activación local",
                "vínculo RackNova Cloud",
                "identidad del nodo",
            ],
        }

    @app.post(
        "/racknova-native/owner-reset",
        include_in_schema=False,
    )
    def owner_reset(
        body: OwnerResetRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(
            default=None,
            alias="X-Empresa-ID",
        ),
    ) -> dict[str, Any]:
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner"},
        )
        empresa_id = str(selected["id_empresa"])

        confirmation = str(body.confirmation or "").strip()
        if confirmation != CONFIRMATION_PHRASE:
            raise HTTPException(
                status_code=400,
                detail=(
                    "La frase de confirmación no coincide. "
                    f"Escribe exactamente: {CONFIRMATION_PHRASE}"
                ),
            )

        password_hash = str(getattr(current_user, "password_hash", "") or "")
        if not password_hash or not verify_password(body.password, password_hash):
            raise HTTPException(
                status_code=403,
                detail="La contraseña del propietario no es correcta.",
            )

        actor = str(
            getattr(current_user, "usuario", None)
            or getattr(current_user, "nombre", None)
            or "owner"
        )

        # Libera la transacción de autorización antes del pg_dump/TRUNCATE.
        session.rollback()

        try:
            backup = _create_backup(
                config,
                secrets,
                actor=actor,
                empresa_id=empresa_id,
            )
            cleared_tables = _reset_database(config, secrets)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "No se pudo completar el borrado local. "
                    "El respaldo previo permanece disponible."
                ),
            ) from exc

        audit = {
            "completed_at": datetime.now().isoformat(),
            "actor": actor,
            "empresa_id": empresa_id,
            "node_code": str(config.get("node_code") or ""),
            "cleared_tables": cleared_tables,
            "preserved_tables": sorted(PRESERVED_TABLES),
        }
        (backup / "RESET_COMPLETED.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "ok": True,
            "message": "Datos operativos locales eliminados correctamente.",
            "backup_path": str(backup),
            "cleared_tables": len(cleared_tables),
            "node_code": str(config.get("node_code") or ""),
        }
