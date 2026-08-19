# ============================================================
# RACKNOVA FASE 2.6 — LOCAL FIRST
# Bootstrap Cloud -> primer nodo Local + registro de nodo + LAN
# ============================================================
from __future__ import annotations

import hmac
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlmodel import Session

import multiempresa_tenant as rn_tenant
from racknova_runtime import load_runtime_config
from racknova_sync_worker import B3_SCHEMA_VERSION, ensure_sync_schema


FASE_26 = "2.6"
BOOTSTRAP_VERSION = 1
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SYNC_INFRA_TABLES = {
    "racknova_nodos",
    "racknova_sync_outbox",
    "racknova_sync_estado",
    "racknova_sync_inbox",
    "racknova_sync_cursor",
    "racknova_sync_id_map",
    "racknova_platform_admins",
}

GLOBAL_TABLES = {
    "empresas",
    "empresa_usuarios",
    "usuario",
}


class LocalNodeRegisterIn(BaseModel):
    empresa_id: str
    node_code: str = Field(min_length=1, max_length=120)
    node_name: str = Field(min_length=1, max_length=180)
    node_type: str = Field(default="LOCAL_SERVER", max_length=30)
    app_version: str | None = Field(default=None, max_length=80)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _sync_secret() -> str:
    return _env("RACKNOVA_SYNC_SECRET")


def _require_sync_secret(received: str | None) -> None:
    expected = _sync_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="RACKNOVA_SYNC_SECRET no está configurado.",
        )
    if not received or not hmac.compare_digest(str(received), expected):
        raise HTTPException(status_code=401, detail="Credencial RackNova Sync inválida.")


def _uuid(value: str, field: str = "UUID") -> str:
    try:
        return str(UUID(str(value)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field} no es UUID válido.") from exc


def _quote_ident(name: str) -> str:
    name = str(name or "")
    if not SAFE_IDENTIFIER.fullmatch(name):
        raise HTTPException(status_code=500, detail=f"Identificador SQL no permitido: {name!r}")
    return f'"{name}"'


def _company_exists(session: Session, empresa_id: str) -> bool:
    value = session.connection().execute(
        sa_text(
            """
            SELECT 1
            FROM empresas
            WHERE id_empresa=CAST(:empresa AS UUID)
              AND activo=TRUE
            LIMIT 1
            """
        ),
        {"empresa": str(empresa_id)},
    ).scalar_one_or_none()
    return bool(value)


def _table_columns(session: Session, table: str) -> list[dict[str, Any]]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT
                column_name,
                data_type,
                udt_name,
                is_nullable,
                column_default,
                is_identity
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=:table
            ORDER BY ordinal_position
            """
        ),
        {"table": table},
    ).mappings().all()
    return [dict(row) for row in rows]



def _table_schema_bundle(session: Session, table: str) -> dict[str, Any]:
    """
    DDL PostgreSQL portable para una tabla del mismo backend.

    Se usa únicamente si la base local no creó una tabla lazy (por ejemplo,
    una tabla POS que se crea la primera vez que se usa su módulo).
    """
    table_q = _quote_ident(table)

    columns = session.connection().execute(
        sa_text(
            """
            SELECT
                a.attname AS column_name,
                format_type(a.atttypid, a.atttypmod) AS type_sql,
                a.attnotnull AS not_null,
                a.attidentity AS identity_kind,
                a.attgenerated AS generated_kind,
                pg_get_expr(ad.adbin, ad.adrelid) AS default_sql
            FROM pg_attribute a
            JOIN pg_class t ON t.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            LEFT JOIN pg_attrdef ad
              ON ad.adrelid=a.attrelid
             AND ad.adnum=a.attnum
            WHERE n.nspname='public'
              AND t.relname=:table
              AND a.attnum>0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        ),
        {"table": table},
    ).mappings().all()

    if not columns:
        raise HTTPException(status_code=500, detail=f"No pude leer esquema de {table}.")

    pre_sql: list[str] = []
    column_defs: list[str] = []
    sequence_seen: set[str] = set()

    for row in columns:
        name = str(row["column_name"])
        name_q = _quote_ident(name)
        type_sql = str(row["type_sql"])
        default_sql = str(row.get("default_sql") or "")
        identity_kind = str(row.get("identity_kind") or "")
        generated_kind = str(row.get("generated_kind") or "")
        not_null = bool(row.get("not_null"))

        definition = f"{name_q} {type_sql}"

        if identity_kind in {"a", "d"}:
            mode = "ALWAYS" if identity_kind == "a" else "BY DEFAULT"
            definition += f" GENERATED {mode} AS IDENTITY"
        elif generated_kind == "s" and default_sql:
            definition += f" GENERATED ALWAYS AS ({default_sql}) STORED"
        elif default_sql:
            match = re.search(
                r"nextval\\('([^']+)'::regclass\\)",
                default_sql,
            )
            if match:
                seq = match.group(1)
                # pg_get_expr devuelve nombres provenientes del catálogo.
                # Aun así restringimos la forma antes de devolver SQL.
                parts = seq.split(".")
                if all(SAFE_IDENTIFIER.fullmatch(part.strip('"')) for part in parts):
                    clean_parts = [part.strip('"') for part in parts]
                    if len(clean_parts) == 1:
                        seq_sql = f'CREATE SEQUENCE IF NOT EXISTS "{clean_parts[0]}"'
                    else:
                        seq_sql = (
                            f'CREATE SEQUENCE IF NOT EXISTS '
                            f'"{clean_parts[-2]}"."{clean_parts[-1]}"'
                        )
                    if seq_sql not in sequence_seen:
                        pre_sql.append(seq_sql)
                        sequence_seen.add(seq_sql)
            definition += f" DEFAULT {default_sql}"

        if not_null:
            definition += " NOT NULL"

        column_defs.append(definition)

    constraints = session.connection().execute(
        sa_text(
            """
            SELECT
                conname,
                contype,
                pg_get_constraintdef(c.oid, true) AS definition
            FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname='public'
              AND t.relname=:table
            ORDER BY
                CASE c.contype
                    WHEN 'p' THEN 0
                    WHEN 'u' THEN 1
                    WHEN 'c' THEN 2
                    WHEN 'f' THEN 3
                    ELSE 4
                END,
                conname
            """
        ),
        {"table": table},
    ).mappings().all()

    constraint_sql: list[str] = []
    for row in constraints:
        name = str(row["conname"])
        if not SAFE_IDENTIFIER.fullmatch(name):
            continue
        definition = str(row["definition"])
        constraint_sql.append(
            f"ALTER TABLE {table_q} ADD CONSTRAINT "
            f"{_quote_ident(name)} {definition}"
        )

    indexes = session.connection().execute(
        sa_text(
            """
            SELECT pg_get_indexdef(i.indexrelid) AS definition
            FROM pg_index i
            JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            LEFT JOIN pg_constraint c ON c.conindid=i.indexrelid
            WHERE n.nspname='public'
              AND t.relname=:table
              AND c.oid IS NULL
            ORDER BY i.indexrelid
            """
        ),
        {"table": table},
    ).mappings().all()

    index_sql = [
        str(row["definition"])
        for row in indexes
        if row.get("definition")
    ]

    return {
        "pre_sql": pre_sql,
        "create_sql": (
            f"CREATE TABLE IF NOT EXISTS {table_q} ("
            + ", ".join(column_defs)
            + ")"
        ),
        "constraints_sql": constraint_sql,
        "indexes_sql": index_sql,
    }


def _tenant_tables(session: Session) -> list[str]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT DISTINCT t.table_name
            FROM information_schema.tables t
            JOIN information_schema.columns c
              ON c.table_schema=t.table_schema
             AND c.table_name=t.table_name
            WHERE t.table_schema='public'
              AND t.table_type='BASE TABLE'
              AND c.column_name='empresa_id'
            ORDER BY t.table_name
            """
        )
    ).scalars().all()

    result: list[str] = []
    for raw in rows:
        table = str(raw)
        if table in SYNC_INFRA_TABLES or table in GLOBAL_TABLES:
            continue
        if SAFE_IDENTIFIER.fullmatch(table):
            result.append(table)
    return result


def _json_rows_for_company(
    session: Session,
    *,
    table: str,
    empresa_id: str,
    max_rows: int,
) -> list[dict[str, Any]]:
    table_q = _quote_ident(table)
    rows = session.connection().execute(
        sa_text(
            f"""
            SELECT to_jsonb(t) AS data
            FROM {table_q} t
            WHERE t.empresa_id=CAST(:empresa AS UUID)
            LIMIT :limit
            """
        ),
        {"empresa": str(empresa_id), "limit": int(max_rows)},
    ).mappings().all()
    return [dict(row["data"]) for row in rows if row.get("data")]


def _company_payload(session: Session, empresa_id: str) -> dict[str, Any]:
    row = session.connection().execute(
        sa_text(
            """
            SELECT to_jsonb(e) AS data
            FROM empresas e
            WHERE id_empresa=CAST(:empresa AS UUID)
            LIMIT 1
            """
        ),
        {"empresa": str(empresa_id)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return dict(row["data"])


def _membership_payload(session: Session, empresa_id: str) -> list[dict[str, Any]]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT to_jsonb(eu) AS data
            FROM empresa_usuarios eu
            WHERE id_empresa=CAST(:empresa AS UUID)
            ORDER BY creado_en, id_membresia
            """
        ),
        {"empresa": str(empresa_id)},
    ).mappings().all()
    return [dict(row["data"]) for row in rows if row.get("data")]


def _company_users_payload(session: Session, empresa_id: str) -> list[dict[str, Any]]:
    """
    Exporta únicamente usuarios que están vinculados a la empresa.
    Los hashes de contraseña se conservan; nunca se exporta una contraseña
    en texto plano porque RackNova no la almacena así.
    """
    rows = session.connection().execute(
        sa_text(
            """
            SELECT DISTINCT to_jsonb(u) AS data
            FROM usuario u
            JOIN empresa_usuarios eu
              ON eu.id_empresa=CAST(:empresa AS UUID)
             AND eu.activo=TRUE
             AND (
                    u.usuario=eu.usuario_key
                 OR CAST(u.id_usuario AS TEXT)=eu.usuario_key
                 OR u.nombre=eu.usuario_key
             )
            WHERE u.activo=TRUE
            ORDER BY 1
            """
        ),
        {"empresa": str(empresa_id)},
    ).mappings().all()
    return [dict(row["data"]) for row in rows if row.get("data")]


def _cloud_cursor(session: Session, empresa_id: str) -> dict[str, Any]:
    row = session.connection().execute(
        sa_text(
            """
            SELECT id_evento, creado_en
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND COALESCE((payload->>'schema_version')::integer,0) >= :schema
              AND payload->>'runtime_mode'='cloud'
            ORDER BY creado_en DESC, id_evento DESC
            LIMIT 1
            """
        ),
        {"empresa": str(empresa_id), "schema": int(B3_SCHEMA_VERSION)},
    ).mappings().first()
    if not row:
        return {"last_event_id": None, "last_created_at": None}
    return {
        "last_event_id": str(row["id_evento"]),
        "last_created_at": row["creado_en"],
    }


def export_company_bootstrap(
    session: Session,
    *,
    empresa_id: str,
    max_rows_per_table: int,
) -> dict[str, Any]:
    empresa_id = _uuid(empresa_id, "empresa_id")
    if not _company_exists(session, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada o inactiva.")

    ensure_sync_schema(session)

    tables: list[dict[str, Any]] = []
    total_rows = 0

    for table in _tenant_tables(session):
        rows = _json_rows_for_company(
            session,
            table=table,
            empresa_id=empresa_id,
            max_rows=max_rows_per_table + 1,
        )
        truncated = len(rows) > max_rows_per_table
        if truncated:
            rows = rows[:max_rows_per_table]
        total_rows += len(rows)

        tables.append(
            {
                "table": table,
                "columns": [
                    row["column_name"]
                    for row in _table_columns(session, table)
                ],
                "schema": _table_schema_bundle(session, table),
                "row_count": len(rows),
                "truncated": truncated,
                "rows": rows,
            }
        )

    if any(item["truncated"] for item in tables):
        names = [item["table"] for item in tables if item["truncated"]]
        raise HTTPException(
            status_code=413,
            detail=(
                "Bootstrap demasiado grande para exportación simple. "
                f"Tablas truncadas: {', '.join(names)}. "
                "Aumenta max_rows_per_table solo de forma controlada."
            ),
        )

    config = load_runtime_config()
    return {
        "fase": FASE_26,
        "bootstrap_version": BOOTSTRAP_VERSION,
        "sync_schema_version": B3_SCHEMA_VERSION,
        "source_runtime_mode": config.mode,
        "source_node_code": config.node_code,
        "source_app_version": config.app_version,
        "generated_at": datetime.now(timezone.utc),
        "empresa_id": empresa_id,
        "company": _company_payload(session, empresa_id),
        "memberships": _membership_payload(session, empresa_id),
        "users": _company_users_payload(session, empresa_id),
        "tables": tables,
        "total_commercial_rows": total_rows,
        "cloud_cursor": _cloud_cursor(session, empresa_id),
    }


def _node_upsert(
    session: Session,
    *,
    empresa_id: str,
    code: str,
    name: str,
    node_type: str,
    app_version: str | None,
) -> dict[str, Any]:
    code = re.sub(r"[^A-Z0-9._-]+", "-", str(code).strip().upper()).strip("-._")[:120]
    if not code:
        raise HTTPException(status_code=400, detail="node_code inválido.")

    node_type = str(node_type or "LOCAL_SERVER").strip().upper()
    if node_type not in {"LOCAL_SERVER", "TERMINAL", "EDGE"}:
        raise HTTPException(
            status_code=400,
            detail="node_type debe ser LOCAL_SERVER, TERMINAL o EDGE.",
        )

    row = session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_nodos (
                empresa_id, codigo, nombre, tipo, activo,
                version_app, ultima_conexion, creado_en, actualizado_en
            )
            VALUES (
                CAST(:empresa AS UUID), :codigo, :nombre, :tipo, TRUE,
                :version, NOW(), NOW(), NOW()
            )
            ON CONFLICT (empresa_id, codigo)
            DO UPDATE SET
                nombre=EXCLUDED.nombre,
                tipo=EXCLUDED.tipo,
                activo=TRUE,
                version_app=EXCLUDED.version_app,
                ultima_conexion=NOW(),
                actualizado_en=NOW()
            RETURNING
                id_nodo, empresa_id, codigo, nombre, tipo, activo,
                version_app, ultima_conexion, creado_en, actualizado_en
            """
        ),
        {
            "empresa": str(empresa_id),
            "codigo": code,
            "nombre": str(name).strip()[:180] or code,
            "tipo": node_type,
            "version": str(app_version).strip()[:80] if app_version else None,
        },
    ).mappings().first()
    session.commit()
    return dict(row)


def _local_ip_candidates() -> list[str]:
    values: list[str] = []
    try:
        host = socket.gethostname()
        for item in socket.getaddrinfo(host, None, family=socket.AF_INET):
            ip = str(item[4][0])
            if ip and not ip.startswith("127.") and ip not in values:
                values.append(ip)
    except Exception:
        pass

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127.") and ip not in values:
            values.insert(0, ip)
    except Exception:
        pass
    return values


def register_local_first_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    @app.get("/sync/v1/bootstrap/export", tags=["RackNova Local 2.6"])
    def racknova_local_bootstrap_export(
        empresa_id: str,
        max_rows_per_table: int = Query(default=100000, ge=1, le=250000),
        x_sync_secret: str | None = Header(
            default=None,
            alias="X-RackNova-Sync-Secret",
        ),
        session: Session = Depends(get_session),
    ):
        _require_sync_secret(x_sync_secret)
        config = load_runtime_config()
        if not config.is_cloud:
            raise HTTPException(
                status_code=409,
                detail="El bootstrap de empresa solo puede exportarse desde Cloud.",
            )
        return export_company_bootstrap(
            session,
            empresa_id=empresa_id,
            max_rows_per_table=max_rows_per_table,
        )

    @app.post("/sync/v1/nodes/register", tags=["RackNova Local 2.6"])
    def racknova_local_node_register(
        body: LocalNodeRegisterIn,
        x_sync_secret: str | None = Header(
            default=None,
            alias="X-RackNova-Sync-Secret",
        ),
        session: Session = Depends(get_session),
    ):
        _require_sync_secret(x_sync_secret)
        config = load_runtime_config()
        if not config.is_cloud:
            raise HTTPException(
                status_code=409,
                detail="El registro remoto del nodo debe realizarse contra Cloud.",
            )

        empresa_id = _uuid(body.empresa_id, "empresa_id")
        if not _company_exists(session, empresa_id):
            raise HTTPException(status_code=404, detail="Empresa no encontrada.")

        ensure_sync_schema(session)
        node = _node_upsert(
            session,
            empresa_id=empresa_id,
            code=body.node_code,
            name=body.node_name,
            node_type=body.node_type,
            app_version=body.app_version,
        )
        return {
            "ok": True,
            "fase": FASE_26,
            "empresa_id": empresa_id,
            "node": node,
        }

    @app.get("/local/v1/status", tags=["RackNova Local 2.6"])
    def racknova_local_status(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin", "operator", "viewer"},
        )
        empresa_id = str(selected["id_empresa"])
        config = load_runtime_config()

        if not config.is_local:
            return {
                "fase": FASE_26,
                "runtime_mode": config.mode,
                "local_active": False,
                "message": "Este proceso es RackNova Cloud; el endpoint LAN se activa en modo local.",
            }

        if str(config.empresa_id or "") != empresa_id:
            raise HTTPException(
                status_code=403,
                detail="Este nodo local está ligado a otra empresa.",
            )

        ensure_sync_schema(session)
        node = session.connection().execute(
            sa_text(
                """
                SELECT
                    id_nodo, codigo, nombre, tipo, activo,
                    version_app, ultima_conexion
                FROM racknova_nodos
                WHERE empresa_id=CAST(:empresa AS UUID)
                  AND codigo=:codigo
                LIMIT 1
                """
            ),
            {"empresa": empresa_id, "codigo": config.node_code},
        ).mappings().first()

        outbox_pending = session.connection().execute(
            sa_text(
                """
                SELECT COUNT(*)
                FROM racknova_sync_outbox
                WHERE empresa_id=CAST(:empresa AS UUID)
                  AND estado='PENDING'
                """
            ),
            {"empresa": empresa_id},
        ).scalar_one()

        return {
            "fase": FASE_26,
            "runtime_mode": "local",
            "local_active": True,
            "empresa_id": empresa_id,
            "node_code": config.node_code,
            "node": dict(node) if node else None,
            "database": "postgresql-local",
            "lan": {
                "bind_host": "0.0.0.0",
                "default_port": 8000,
                "ip_candidates": _local_ip_candidates(),
                "urls": [
                    f"http://{ip}:8000"
                    for ip in _local_ip_candidates()
                ],
            },
            "sync": {
                "cloud_url_configured": bool(_env("RACKNOVA_CLOUD_URL")),
                "secret_configured": bool(_sync_secret()),
                "pending": int(outbox_pending or 0),
            },
        }
