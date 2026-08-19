# ============================================================
# RACKNOVA FASE 2.5 - BLOQUE A
# Runtime Local / Cloud + identidad de nodo
# ============================================================
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException
from sqlalchemy import text as sa_text
from sqlmodel import Session

import multiempresa_tenant as rn_tenant


VALID_MODES = {"cloud", "local"}
VALID_NODE_TYPES = {"CLOUD", "LOCAL_SERVER", "TERMINAL", "EDGE"}


def _clean_node_code(value: str) -> str:
    value = (value or "").strip().upper()
    value = re.sub(r"[^A-Z0-9._-]+", "-", value)
    value = value.strip("-._")
    return value[:120]


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str
    node_code: str
    node_name: str
    node_type: str
    empresa_id: str | None
    app_version: str
    node_code_is_explicit: bool

    @property
    def is_cloud(self) -> bool:
        return self.mode == "cloud"

    @property
    def is_local(self) -> bool:
        return self.mode == "local"

    def warnings(self) -> list[str]:
        result: list[str] = []

        if self.mode not in VALID_MODES:
            result.append(
                f"RACKNOVA_MODE inválido: {self.mode!r}. Usa 'cloud' o 'local'."
            )

        if not self.node_code:
            result.append("No se pudo determinar RACKNOVA_NODE_CODE.")

        if self.node_type not in VALID_NODE_TYPES:
            result.append(
                f"RACKNOVA_NODE_TYPE inválido: {self.node_type!r}."
            )

        if self.is_local and not self.empresa_id:
            result.append(
                "Modo local sin RACKNOVA_EMPRESA_ID. "
                "Antes de operar una instalación local debe ligarse a una empresa."
            )

        if self.is_local and not self.node_code_is_explicit:
            result.append(
                "RACKNOVA_NODE_CODE fue generado automáticamente. "
                "En una instalación real conviene fijarlo en el archivo de configuración."
            )

        return result


def load_runtime_config() -> RuntimeConfig:
    mode = _env("RACKNOVA_MODE", "cloud").lower()

    explicit_node_code = bool(_env("RACKNOVA_NODE_CODE"))
    if explicit_node_code:
        node_code = _clean_node_code(_env("RACKNOVA_NODE_CODE"))
    elif mode == "cloud":
        node_code = "CLOUD"
    else:
        host = _clean_node_code(socket.gethostname()) or "SERVER"
        node_code = f"LOCAL-{host}"[:120]

    default_name = (
        "RackNova Cloud"
        if mode == "cloud"
        else f"RackNova Local - {socket.gethostname()}"
    )
    node_name = _env("RACKNOVA_NODE_NAME", default_name)[:180]

    default_type = "CLOUD" if mode == "cloud" else "LOCAL_SERVER"
    node_type = _env("RACKNOVA_NODE_TYPE", default_type).upper()

    empresa_id = _env("RACKNOVA_EMPRESA_ID") or None

    app_version = (
        _env("RACKNOVA_APP_VERSION")
        or _env("RENDER_GIT_COMMIT")
        or "dev"
    )
    if len(app_version) > 80:
        app_version = app_version[:80]

    return RuntimeConfig(
        mode=mode,
        node_code=node_code,
        node_name=node_name,
        node_type=node_type,
        empresa_id=empresa_id,
        app_version=app_version,
        node_code_is_explicit=explicit_node_code,
    )


def _enforce_local_company(
    config: RuntimeConfig,
    selected_company_id: str,
) -> None:
    if not config.is_local:
        return

    if not config.empresa_id:
        raise HTTPException(
            status_code=503,
            detail=(
                "RackNova Local todavía no tiene RACKNOVA_EMPRESA_ID configurado."
            ),
        )

    if str(config.empresa_id) != str(selected_company_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "Este nodo local está ligado a otra empresa y no puede "
                "operar con el X-Empresa-ID solicitado."
            ),
        )


def _database_ok(session: Session) -> tuple[bool, str | None]:
    try:
        session.connection().execute(sa_text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, str(exc)


def _node_row(
    session: Session,
    empresa_id: str,
    node_code: str,
) -> dict[str, Any] | None:
    row = session.connection().execute(
        sa_text(
            """
            SELECT
                id_nodo,
                empresa_id,
                codigo,
                nombre,
                tipo,
                activo,
                version_app,
                ultima_conexion,
                creado_en,
                actualizado_en
            FROM racknova_nodos
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND codigo = :codigo
            LIMIT 1
            """
        ),
        {
            "empresa_id": str(empresa_id),
            "codigo": node_code,
        },
    ).mappings().first()

    return dict(row) if row else None


def ensure_node_registered(
    session: Session,
    empresa_id: str,
    config: RuntimeConfig | None = None,
) -> dict[str, Any]:
    config = config or load_runtime_config()

    if config.mode not in VALID_MODES:
        raise HTTPException(
            status_code=500,
            detail="RACKNOVA_MODE debe ser 'cloud' o 'local'.",
        )

    if config.node_type not in VALID_NODE_TYPES:
        raise HTTPException(
            status_code=500,
            detail="RACKNOVA_NODE_TYPE no es válido.",
        )

    row = session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_nodos (
                empresa_id,
                codigo,
                nombre,
                tipo,
                activo,
                version_app,
                ultima_conexion,
                creado_en,
                actualizado_en
            )
            VALUES (
                CAST(:empresa_id AS UUID),
                :codigo,
                :nombre,
                :tipo,
                TRUE,
                :version_app,
                NOW(),
                NOW(),
                NOW()
            )
            ON CONFLICT (empresa_id, codigo)
            DO UPDATE SET
                nombre = EXCLUDED.nombre,
                tipo = EXCLUDED.tipo,
                activo = TRUE,
                version_app = EXCLUDED.version_app,
                ultima_conexion = NOW(),
                actualizado_en = NOW()
            RETURNING
                id_nodo,
                empresa_id,
                codigo,
                nombre,
                tipo,
                activo,
                version_app,
                ultima_conexion,
                creado_en,
                actualizado_en
            """
        ),
        {
            "empresa_id": str(empresa_id),
            "codigo": config.node_code,
            "nombre": config.node_name,
            "tipo": config.node_type,
            "version_app": config.app_version,
        },
    ).mappings().first()

    session.commit()
    return dict(row)


def runtime_status_payload(
    session: Session,
    empresa_id: str,
) -> dict[str, Any]:
    config = load_runtime_config()
    db_ok, db_error = _database_ok(session)

    node = None
    if db_ok:
        try:
            node = _node_row(
                session=session,
                empresa_id=empresa_id,
                node_code=config.node_code,
            )
        except Exception:
            node = None

    try:
        dialect = str(session.get_bind().dialect.name)
    except Exception:
        dialect = "unknown"

    warnings = config.warnings()

    return {
        "fase": "2.5",
        "bloque": "A",
        "runtime": {
            "mode": config.mode,
            "is_cloud": config.is_cloud,
            "is_local": config.is_local,
            "app_version": config.app_version,
        },
        "empresa_id": str(empresa_id),
        "node": {
            "code": config.node_code,
            "name": config.node_name,
            "type": config.node_type,
            "configured_empresa_id": config.empresa_id,
            "registered": node is not None,
            "record": node,
        },
        "database": {
            "ok": db_ok,
            "dialect": dialect,
            "error": db_error,
        },
        "sync": {
            "implemented": True,
            "note": "RackNova Sync B3 está instalado; el transporte requiere configuración de entorno.",
        },
        "warnings": warnings,
        "ready_for_block_b": bool(
            db_ok
            and config.mode in VALID_MODES
            and config.node_type in VALID_NODE_TYPES
            and (
                config.is_cloud
                or (config.is_local and bool(config.empresa_id))
            )
        ),
    }


def register_runtime_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    """
    Registra endpoints de runtime sin crear un segundo backend.
    """

    @app.get(
        "/runtime/status",
        tags=["RackNova Runtime"],
    )
    def racknova_runtime_status(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(
            default=None,
            alias="X-Empresa-ID",
        ),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin", "operator", "viewer"},
        )
        empresa_id = str(selected["id_empresa"])
        config = load_runtime_config()
        _enforce_local_company(config, empresa_id)

        return runtime_status_payload(
            session=session,
            empresa_id=empresa_id,
        )

    @app.post(
        "/runtime/register-node",
        tags=["RackNova Runtime"],
    )
    def racknova_runtime_register_node(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(
            default=None,
            alias="X-Empresa-ID",
        ),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin"},
        )
        empresa_id = str(selected["id_empresa"])
        config = load_runtime_config()
        _enforce_local_company(config, empresa_id)

        node = ensure_node_registered(
            session=session,
            empresa_id=empresa_id,
            config=config,
        )

        return {
            "mensaje": "Nodo RackNova registrado/actualizado.",
            "runtime_mode": config.mode,
            "empresa_id": empresa_id,
            "node": node,
        }
