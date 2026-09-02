# ============================================================
# RACKNOVA FASE 2.5 — BLOQUE B3 COMPLETO
# Transporte Local <-> Cloud, inbox, idempotencia, pull y worker
# ============================================================
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlmodel import Session

import multiempresa_tenant as rn_tenant
from racknova_runtime import ensure_node_registered, load_runtime_config
from racknova_ai_relay import (
    RackNovaAICloudCompletionRequest,
    request_deepseek_from_cloud,
)


B3_SCHEMA_VERSION = 4
MAX_BATCH = 25
DEFAULT_INTERVAL_SECONDS = 15
MAX_ATTEMPTS = 5
STALE_SENDING_MINUTES = 5

COMMERCIAL_PREFIXES = (
    "pos_",
    "producto",
    "product",
    "inventario",
    "inventory",
    "movimiento",
    "movement",
    "venta",
    "sale",
    "cliente",
    "customer",
    "credito",
    "credit",
    "abono",
    "devolucion",
    "catalogo",
    "cotizacion",
    "ubicacion",
    "location",
    "rack",
    "alerta",
    "lote",
)

EXCLUDED_TABLES = {
    "empresas",
    "empresa_usuarios",
    "usuario",
    "racknova_platform_admins",
    "racknova_nodos",
    "racknova_sync_outbox",
    "racknova_sync_estado",
    "racknova_sync_inbox",
    "racknova_sync_cursor",
    "racknova_sync_id_map",
}

# Cloud -> Local: por ahora solo datos cuyo dueño lógico puede ser Cloud.
PULL_ENTITY_PREFIXES = (
    "config.",
    "catalog.",
    "customer.",
    "inventory.",
)

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


class SyncEventIn(BaseModel):
    id_evento: str
    empresa_id: str
    entidad: str
    operacion: str = "EVENT"
    payload: dict[str, Any] = Field(default_factory=dict)
    entidad_sync_uuid: str | None = None
    creado_en: str | None = None


class SyncBatchIn(BaseModel):
    origin_node_code: str
    origin_node_name: str | None = None
    origin_node_type: str | None = None
    app_version: str | None = None
    events: list[SyncEventIn]


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "si", "sí", "on"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _env(name)
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_uuid(value: str, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field} no es UUID válido.") from exc


def _quote_ident(value: str) -> str:
    value = str(value or "")
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"Identificador SQL no permitido: {value!r}")
    return f'"{value}"'


def _is_commercial_table(table: str) -> bool:
    table = str(table or "").lower()
    return (
        bool(table)
        and table not in EXCLUDED_TABLES
        and table.startswith(COMMERCIAL_PREFIXES)
    )


def _bind_empresa_system(session: Session, empresa_id: str) -> None:
    key = getattr(rn_tenant, "SESSION_EMPRESA_KEY", "racknova_empresa_id")
    session.info[key] = str(empresa_id)
    session.connection().execute(
        sa_text("SELECT set_config('app.racknova_empresa_id', :empresa, true)"),
        {"empresa": str(empresa_id)},
    )


def _sync_secret() -> str:
    return _env("RACKNOVA_SYNC_SECRET")


def _require_sync_secret(received: str | None) -> None:
    expected = _sync_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "RACKNOVA_SYNC_SECRET no está configurado en este nodo. "
                "El transporte RackNova Sync permanece bloqueado."
            ),
        )
    if not received or received != expected:
        raise HTTPException(status_code=401, detail="Credencial RackNova Sync inválida.")


def _company_exists(session: Session, empresa_id: str) -> bool:
    value = session.connection().execute(
        sa_text(
            """
            SELECT 1
            FROM empresas
            WHERE id_empresa = CAST(:empresa AS UUID)
              AND activo = TRUE
            LIMIT 1
            """
        ),
        {"empresa": str(empresa_id)},
    ).scalar_one_or_none()
    return bool(value)


def ensure_sync_schema(session: Session) -> None:
    """
    Crea infraestructura B3 si aún no existe.

    También instala trigger genérico de revisión sobre tablas comerciales.
    Cuando app.racknova_sync_apply=1, una réplica recibida conserva la revisión
    que venía del nodo de origen y NO crea una revisión nueva.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

        ddl = r"""
        CREATE TABLE IF NOT EXISTS racknova_sync_inbox (
            id_evento UUID PRIMARY KEY,
            empresa_id UUID NOT NULL REFERENCES empresas(id_empresa) ON DELETE CASCADE,
            origin_node_code VARCHAR(120) NOT NULL,
            entidad VARCHAR(120) NOT NULL,
            operacion VARCHAR(20) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            estado VARCHAR(20) NOT NULL DEFAULT 'RECEIVED',
            ultimo_error TEXT,
            recibido_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            aplicado_en TIMESTAMPTZ,
            CONSTRAINT racknova_sync_inbox_estado_check
                CHECK (estado IN ('RECEIVED','APPLIED','IGNORED','ERROR'))
        );

        CREATE INDEX IF NOT EXISTS ix_racknova_sync_inbox_empresa_fecha
            ON racknova_sync_inbox (empresa_id, recibido_en DESC);

        CREATE TABLE IF NOT EXISTS racknova_sync_cursor (
            empresa_id UUID NOT NULL REFERENCES empresas(id_empresa) ON DELETE CASCADE,
            node_code VARCHAR(120) NOT NULL,
            direccion VARCHAR(20) NOT NULL,
            last_created_at TIMESTAMPTZ,
            last_event_id UUID,
            actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (empresa_id, node_code, direccion),
            CONSTRAINT racknova_sync_cursor_direccion_check
                CHECK (direccion IN ('CLOUD_TO_LOCAL','LOCAL_TO_CLOUD'))
        );

        CREATE TABLE IF NOT EXISTS racknova_sync_id_map (
            empresa_id UUID NOT NULL REFERENCES empresas(id_empresa) ON DELETE CASCADE,
            origin_node_code VARCHAR(120) NOT NULL,
            table_name VARCHAR(120) NOT NULL,
            source_pk_text TEXT NOT NULL,
            target_pk JSONB NOT NULL,
            sync_uuid UUID,
            creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (
                empresa_id,
                origin_node_code,
                table_name,
                source_pk_text
            )
        );

        CREATE INDEX IF NOT EXISTS ix_racknova_sync_id_map_sync_uuid
            ON racknova_sync_id_map (empresa_id, table_name, sync_uuid);

        CREATE OR REPLACE FUNCTION racknova_sync_touch_row()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF current_setting('app.racknova_sync_apply', true) = '1' THEN
                RETURN NEW;
            END IF;

            IF TG_OP = 'INSERT' THEN
                NEW.sync_revision := GREATEST(COALESCE(NEW.sync_revision, 0), 1);
            ELSE
                NEW.sync_revision := COALESCE(OLD.sync_revision, 0) + 1;
            END IF;

            NEW.sync_updated_at := NOW();
            RETURN NEW;
        END;
        $$;

        DO $$
        DECLARE
            r RECORD;
            trigger_name TEXT;
        BEGIN
            FOR r IN
                SELECT t.table_name
                FROM information_schema.tables t
                WHERE t.table_schema = 'public'
                  AND t.table_type = 'BASE TABLE'
                  AND t.table_name NOT IN (
                      'empresas',
                      'empresa_usuarios',
                      'usuario',
                      'racknova_platform_admins',
                      'racknova_nodos',
                      'racknova_sync_outbox',
                      'racknova_sync_estado',
                      'racknova_sync_inbox',
                      'racknova_sync_cursor',
                      'racknova_sync_id_map'
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM information_schema.columns c
                      WHERE c.table_schema='public'
                        AND c.table_name=t.table_name
                        AND c.column_name='sync_revision'
                  )
                  AND (
                      t.table_name LIKE 'pos_%'
                      OR t.table_name LIKE 'producto%'
                      OR t.table_name LIKE 'product%'
                      OR t.table_name LIKE 'inventario%'
                      OR t.table_name LIKE 'inventory%'
                      OR t.table_name LIKE 'movimiento%'
                      OR t.table_name LIKE 'movement%'
                      OR t.table_name LIKE 'venta%'
                      OR t.table_name LIKE 'sale%'
                      OR t.table_name LIKE 'cliente%'
                      OR t.table_name LIKE 'customer%'
                      OR t.table_name LIKE 'credito%'
                      OR t.table_name LIKE 'credit%'
                      OR t.table_name LIKE 'abono%'
                      OR t.table_name LIKE 'devolucion%'
                      OR t.table_name LIKE 'catalogo%'
                      OR t.table_name LIKE 'cotizacion%'
                      OR t.table_name LIKE 'ubicacion%'
                      OR t.table_name LIKE 'location%'
                      OR t.table_name LIKE 'rack%'
                      OR t.table_name LIKE 'alerta%'
                      OR t.table_name LIKE 'lote%'
                  )
            LOOP
                trigger_name := left('trg_rn_sync_touch_' || r.table_name, 63);
                EXECUTE format(
                    'DROP TRIGGER IF EXISTS %I ON public.%I',
                    trigger_name,
                    r.table_name
                );
                EXECUTE format(
                    'CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON public.%I '
                    'FOR EACH ROW EXECUTE FUNCTION racknova_sync_touch_row()',
                    trigger_name,
                    r.table_name
                );
            END LOOP;
        END $$;
        """

        # psycopg permite el bloque múltiple usado por SQLAlchemy text en PostgreSQL.
        session.connection().execute(sa_text(ddl))
        session.commit()
        _SCHEMA_READY = True


def _node_upsert_without_commit(
    session: Session,
    *,
    empresa_id: str,
    code: str,
    name: str | None,
    node_type: str | None,
    app_version: str | None,
) -> str:
    code = str(code or "").strip().upper()[:120]
    if not code:
        raise HTTPException(status_code=400, detail="origin_node_code es obligatorio.")

    node_type = str(node_type or "LOCAL_SERVER").strip().upper()
    if node_type not in {"CLOUD", "LOCAL_SERVER", "TERMINAL", "EDGE"}:
        node_type = "LOCAL_SERVER"

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
                nombre = EXCLUDED.nombre,
                tipo = EXCLUDED.tipo,
                activo = TRUE,
                version_app = EXCLUDED.version_app,
                ultima_conexion = NOW(),
                actualizado_en = NOW()
            RETURNING id_nodo
            """
        ),
        {
            "empresa": str(empresa_id),
            "codigo": code,
            "nombre": (name or code)[:180],
            "tipo": node_type,
            "version": (app_version or "")[:80] or None,
        },
    ).mappings().first()

    if not row:
        raise RuntimeError("No se pudo registrar el nodo remoto.")
    return str(row["id_nodo"])


def _table_meta(session: Session, table: str) -> dict[str, Any]:
    if not _is_commercial_table(table):
        raise ValueError(f"Tabla no sincronizable: {table!r}")

    columns = session.connection().execute(
        sa_text(
            """
            SELECT
                column_name,
                column_default,
                is_identity,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=:table
            ORDER BY ordinal_position
            """
        ),
        {"table": table},
    ).mappings().all()

    if not columns:
        raise ValueError(f"La tabla {table!r} no existe.")

    column_names = [str(row["column_name"]) for row in columns]
    if "empresa_id" not in column_names or "sync_uuid" not in column_names:
        raise ValueError(f"La tabla {table!r} no tiene identidad multiempresa/sync.")

    pk_rows = session.connection().execute(
        sa_text(
            """
            SELECT a.attname AS column_name
            FROM pg_index i
            JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON TRUE
            JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
            WHERE n.nspname='public'
              AND t.relname=:table
              AND i.indisprimary=TRUE
            ORDER BY k.ord
            """
        ),
        {"table": table},
    ).mappings().all()
    pk = [str(row["column_name"]) for row in pk_rows]

    fk_rows = session.connection().execute(
        sa_text(
            """
            SELECT
                a.attname AS local_column,
                rt.relname AS remote_table,
                ra.attname AS remote_column
            FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_class rt ON rt.oid=c.confrelid
            JOIN unnest(c.conkey) WITH ORDINALITY lk(attnum, ord) ON TRUE
            JOIN unnest(c.confkey) WITH ORDINALITY rk(attnum, ord)
              ON rk.ord=lk.ord
            JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=lk.attnum
            JOIN pg_attribute ra ON ra.attrelid=rt.oid AND ra.attnum=rk.attnum
            WHERE n.nspname='public'
              AND t.relname=:table
              AND c.contype='f'
            """
        ),
        {"table": table},
    ).mappings().all()

    generated = set()
    for row in columns:
        name = str(row["column_name"])
        default = str(row.get("column_default") or "")
        identity = str(row.get("is_identity") or "").upper() == "YES"
        if identity or default.startswith("nextval("):
            generated.add(name)

    return {
        "columns": set(column_names),
        "pk": pk,
        "generated_pk": generated.intersection(pk),
        "fks": [dict(row) for row in fk_rows],
    }


def _pk_text(pk: dict[str, Any]) -> str:
    return _json_dumps(pk or {})


def _map_get(
    session: Session,
    *,
    empresa_id: str,
    origin_node_code: str,
    table: str,
    source_pk: dict[str, Any],
) -> dict[str, Any] | None:
    row = session.connection().execute(
        sa_text(
            """
            SELECT target_pk
            FROM racknova_sync_id_map
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND origin_node_code=:node
              AND table_name=:table
              AND source_pk_text=:source
            LIMIT 1
            """
        ),
        {
            "empresa": str(empresa_id),
            "node": str(origin_node_code),
            "table": table,
            "source": _pk_text(source_pk),
        },
    ).mappings().first()
    return dict(row["target_pk"]) if row and row["target_pk"] else None


def _map_put(
    session: Session,
    *,
    empresa_id: str,
    origin_node_code: str,
    table: str,
    source_pk: dict[str, Any],
    target_pk: dict[str, Any],
    sync_uuid: str | None,
) -> None:
    session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_id_map (
                empresa_id, origin_node_code, table_name,
                source_pk_text, target_pk, sync_uuid,
                creado_en, actualizado_en
            )
            VALUES (
                CAST(:empresa AS UUID), :node, :table,
                :source, CAST(:target AS JSONB), CAST(:sync_uuid AS UUID),
                NOW(), NOW()
            )
            ON CONFLICT (
                empresa_id, origin_node_code, table_name, source_pk_text
            )
            DO UPDATE SET
                target_pk=EXCLUDED.target_pk,
                sync_uuid=COALESCE(EXCLUDED.sync_uuid, racknova_sync_id_map.sync_uuid),
                actualizado_en=NOW()
            """
        ),
        {
            "empresa": str(empresa_id),
            "node": str(origin_node_code),
            "table": table,
            "source": _pk_text(source_pk),
            "target": _json_dumps(target_pk),
            "sync_uuid": str(sync_uuid) if sync_uuid else None,
        },
    )


def _row_by_sync_uuid(
    session: Session,
    *,
    table: str,
    empresa_id: str,
    sync_uuid: str,
    pk_columns: list[str],
) -> dict[str, Any] | None:
    table_q = _quote_ident(table)
    pk_select = ", ".join(_quote_ident(c) for c in pk_columns) if pk_columns else "sync_uuid"
    sql = (
        f"SELECT {pk_select}, sync_revision, sync_updated_at "
        f"FROM {table_q} "
        "WHERE empresa_id=CAST(:empresa AS UUID) "
        "AND sync_uuid=CAST(:sync_uuid AS UUID) LIMIT 1"
    )
    row = session.connection().execute(
        sa_text(sql),
        {"empresa": str(empresa_id), "sync_uuid": str(sync_uuid)},
    ).mappings().first()
    return dict(row) if row else None


def _row_by_pk(
    session: Session,
    *,
    table: str,
    empresa_id: str,
    pk: dict[str, Any],
) -> dict[str, Any] | None:
    if not pk:
        return None
    clauses = []
    params: dict[str, Any] = {"empresa": str(empresa_id)}
    for i, (key, value) in enumerate(pk.items()):
        _quote_ident(key)
        params[f"v{i}"] = value
        clauses.append(f"{_quote_ident(key)}=:v{i}")
    table_q = _quote_ident(table)
    row = session.connection().execute(
        sa_text(
            f"SELECT to_jsonb(t) AS data FROM {table_q} t "
            "WHERE empresa_id=CAST(:empresa AS UUID) "
            f"AND {' AND '.join(clauses)} LIMIT 1"
        ),
        params,
    ).mappings().first()
    return dict(row["data"]) if row and row["data"] else None


def _resolve_fk_values(
    session: Session,
    *,
    empresa_id: str,
    origin_node_code: str,
    table: str,
    data: dict[str, Any],
    meta: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    out = dict(data)
    missing: list[str] = []

    for fk in meta["fks"]:
        local_col = str(fk["local_column"])
        remote_table = str(fk["remote_table"])
        remote_col = str(fk["remote_column"])
        source_value = out.get(local_col)

        if source_value is None:
            continue
        if not _is_commercial_table(remote_table):
            continue

        source_pk = {remote_col: source_value}
        mapped = _map_get(
            session,
            empresa_id=empresa_id,
            origin_node_code=origin_node_code,
            table=remote_table,
            source_pk=source_pk,
        )
        if mapped and remote_col in mapped:
            out[local_col] = mapped[remote_col]
            continue

        # Baseline clonado: si el mismo PK ya existe en la empresa, podemos
        # aceptar ese destino y registrar el mapa para futuras referencias.
        baseline = _row_by_pk(
            session,
            table=remote_table,
            empresa_id=empresa_id,
            pk=source_pk,
        )
        if baseline:
            out[local_col] = source_value
            remote_sync = baseline.get("sync_uuid")
            _map_put(
                session,
                empresa_id=empresa_id,
                origin_node_code=origin_node_code,
                table=remote_table,
                source_pk=source_pk,
                target_pk=source_pk,
                sync_uuid=str(remote_sync) if remote_sync else None,
            )
            continue

        missing.append(f"{table}.{local_col}->{remote_table}.{remote_col}:{source_value}")

    return out, missing


def _insert_json_record(
    session: Session,
    *,
    table: str,
    meta: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    table_q = _quote_ident(table)
    columns = [c for c in data if c in meta["columns"]]

    # Si una PK autogenerada ya está ocupada por otra entidad, dejamos que
    # PostgreSQL asigne un nuevo ID y guardamos el mapeo source -> target.
    for pk_col in list(meta["generated_pk"]):
        if pk_col not in data:
            continue

    if not columns:
        raise ValueError(f"No hay columnas aplicables para {table}.")

    cols_sql = ", ".join(_quote_ident(c) for c in columns)
    select_sql = ", ".join(f'src.{_quote_ident(c)}' for c in columns)
    returning = ", ".join(_quote_ident(c) for c in meta["pk"]) or "sync_uuid"

    sql = (
        f"INSERT INTO {table_q} ({cols_sql}) "
        f"SELECT {select_sql} "
        f"FROM jsonb_populate_record(NULL::{table_q}, CAST(:data AS JSONB)) AS src "
        f"RETURNING {returning}"
    )
    row = session.connection().execute(
        sa_text(sql),
        {"data": _json_dumps(data)},
    ).mappings().first()
    return dict(row) if row else {}


def _update_json_record(
    session: Session,
    *,
    table: str,
    meta: dict[str, Any],
    empresa_id: str,
    sync_uuid: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    table_q = _quote_ident(table)
    immutable = set(meta["pk"]) | {"empresa_id", "sync_uuid"}
    columns = [
        c for c in data
        if c in meta["columns"] and c not in immutable
    ]
    returning = ", ".join(
        f"dst.{_quote_ident(c)}" for c in meta["pk"]
    ) or "dst.sync_uuid"

    if not columns:
        existing = _row_by_sync_uuid(
            session,
            table=table,
            empresa_id=empresa_id,
            sync_uuid=sync_uuid,
            pk_columns=meta["pk"],
        )
        return {
            key: existing.get(key)
            for key in meta["pk"]
            if existing and key in existing
        }

    set_sql = ", ".join(
        f'{_quote_ident(c)}=src.{_quote_ident(c)}'
        for c in columns
    )
    sql = (
        f"UPDATE {table_q} AS dst SET {set_sql} "
        f"FROM jsonb_populate_record(NULL::{table_q}, CAST(:data AS JSONB)) AS src "
        "WHERE dst.empresa_id=CAST(:empresa AS UUID) "
        "AND dst.sync_uuid=CAST(:sync_uuid AS UUID) "
        f"RETURNING {returning}"
    )
    row = session.connection().execute(
        sa_text(sql),
        {
            "data": _json_dumps(data),
            "empresa": str(empresa_id),
            "sync_uuid": str(sync_uuid),
        },
    ).mappings().first()
    return dict(row) if row else {}


def _apply_record(
    session: Session,
    *,
    empresa_id: str,
    origin_node_code: str,
    record: dict[str, Any],
    delete_mode: bool,
    origin_node_id: str | None,
) -> dict[str, Any]:
    table = str(record.get("table") or "")
    if not _is_commercial_table(table):
        return {"status": "IGNORED", "reason": "non_commercial", "table": table}

    meta = _table_meta(session, table)
    source_pk = dict(record.get("pk") or {})
    data = dict(record.get("data") or {})
    sync_uuid = (
        record.get("sync_uuid")
        or data.get("sync_uuid")
    )
    if not sync_uuid:
        raise ValueError(f"{table}: el registro no contiene sync_uuid.")

    sync_uuid = str(UUID(str(sync_uuid)))
    incoming_revision = record.get("sync_revision")
    if incoming_revision is None:
        incoming_revision = data.get("sync_revision")
    if incoming_revision is None:
        raise ValueError(
            f"{table}: evento schema {B3_SCHEMA_VERSION} sin sync_revision."
        )
    incoming_revision = int(incoming_revision)

    existing = _row_by_sync_uuid(
        session,
        table=table,
        empresa_id=empresa_id,
        sync_uuid=sync_uuid,
        pk_columns=meta["pk"],
    )

    if delete_mode:
        if not existing:
            # Borrado idempotente: ya no existe.
            return {"status": "APPLIED", "action": "already_deleted", "table": table}

        table_q = _quote_ident(table)
        session.connection().execute(
            sa_text(
                f"DELETE FROM {table_q} "
                "WHERE empresa_id=CAST(:empresa AS UUID) "
                "AND sync_uuid=CAST(:sync_uuid AS UUID)"
            ),
            {"empresa": str(empresa_id), "sync_uuid": sync_uuid},
        )
        return {"status": "APPLIED", "action": "deleted", "table": table}

    local_revision = int(existing.get("sync_revision") or 0) if existing else -1
    if existing and local_revision > incoming_revision:
        return {
            "status": "IGNORED",
            "action": "newer_destination",
            "table": table,
            "local_revision": local_revision,
            "incoming_revision": incoming_revision,
        }

    data["empresa_id"] = str(empresa_id)
    data["sync_uuid"] = sync_uuid
    data["sync_revision"] = incoming_revision
    if record.get("sync_updated_at") and not data.get("sync_updated_at"):
        data["sync_updated_at"] = record.get("sync_updated_at")
    if origin_node_id:
        data["sync_origen_nodo"] = str(origin_node_id)

    data, missing = _resolve_fk_values(
        session,
        empresa_id=empresa_id,
        origin_node_code=origin_node_code,
        table=table,
        data=data,
        meta=meta,
    )
    if missing:
        return {
            "status": "DEFERRED",
            "table": table,
            "missing": missing,
        }

    # Si no existe por UUID y una PK autogenerada fuente choca con otra fila,
    # se elimina esa PK del INSERT para que el destino genere la suya.
    if not existing:
        for pk_col in meta["generated_pk"]:
            if pk_col not in data:
                continue
            pk_candidate = {pk_col: data[pk_col]}
            occupied = _row_by_pk(
                session,
                table=table,
                empresa_id=empresa_id,
                pk=pk_candidate,
            )
            if occupied and str(occupied.get("sync_uuid") or "") != sync_uuid:
                data.pop(pk_col, None)

    if existing:
        target_pk = _update_json_record(
            session,
            table=table,
            meta=meta,
            empresa_id=empresa_id,
            sync_uuid=sync_uuid,
            data=data,
        )
        action = "updated"
    else:
        target_pk = _insert_json_record(
            session,
            table=table,
            meta=meta,
            data=data,
        )
        action = "inserted"

    if not target_pk and meta["pk"]:
        refreshed = _row_by_sync_uuid(
            session,
            table=table,
            empresa_id=empresa_id,
            sync_uuid=sync_uuid,
            pk_columns=meta["pk"],
        )
        target_pk = {
            key: refreshed.get(key)
            for key in meta["pk"]
            if refreshed and key in refreshed
        }

    if source_pk and target_pk:
        _map_put(
            session,
            empresa_id=empresa_id,
            origin_node_code=origin_node_code,
            table=table,
            source_pk=source_pk,
            target_pk=target_pk,
            sync_uuid=sync_uuid,
        )

    return {
        "status": "APPLIED",
        "action": action,
        "table": table,
        "source_pk": source_pk,
        "target_pk": target_pk,
        "sync_uuid": sync_uuid,
        "revision": incoming_revision,
    }


def _apply_event(
    session: Session,
    *,
    empresa_id: str,
    origin_node_code: str,
    origin_node_id: str | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(event.get("payload") or {})
    schema_version = int(payload.get("schema_version") or 0)
    if schema_version < B3_SCHEMA_VERSION:
        raise ValueError(
            f"Evento legacy schema_version={schema_version}; B3 requiere >= {B3_SCHEMA_VERSION}."
        )

    event_id = _validate_uuid(str(event.get("id_evento")), "id_evento")
    entity = str(event.get("entidad") or payload.get("event_type") or "")
    operation = str(event.get("operacion") or payload.get("operation") or "EVENT").upper()
    records = list(payload.get("records") or [])

    existing = session.connection().execute(
        sa_text(
            """
            SELECT estado, ultimo_error
            FROM racknova_sync_inbox
            WHERE id_evento=CAST(:id AS UUID)
            LIMIT 1
            """
        ),
        {"id": event_id},
    ).mappings().first()

    if existing and str(existing["estado"]) in {"APPLIED", "IGNORED"}:
        return {
            "id_evento": event_id,
            "status": "DUPLICATE",
            "previous_status": str(existing["estado"]),
        }

    session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_inbox (
                id_evento, empresa_id, origin_node_code,
                entidad, operacion, payload, estado,
                ultimo_error, recibido_en, aplicado_en
            )
            VALUES (
                CAST(:id AS UUID), CAST(:empresa AS UUID), :node,
                :entidad, :operacion, CAST(:payload AS JSONB), 'RECEIVED',
                NULL, NOW(), NULL
            )
            ON CONFLICT (id_evento)
            DO UPDATE SET
                payload=EXCLUDED.payload,
                estado='RECEIVED',
                ultimo_error=NULL
            """
        ),
        {
            "id": event_id,
            "empresa": str(empresa_id),
            "node": origin_node_code,
            "entidad": entity,
            "operacion": operation,
            "payload": _json_dumps(payload),
        },
    )

    if not records:
        session.connection().execute(
            sa_text(
                """
                UPDATE racknova_sync_inbox
                SET estado='IGNORED', aplicado_en=NOW()
                WHERE id_evento=CAST(:id AS UUID)
                """
            ),
            {"id": event_id},
        )
        return {
            "id_evento": event_id,
            "status": "IGNORED",
            "reason": "event_without_records",
        }

    session.connection().execute(
        sa_text("SELECT set_config('app.racknova_sync_apply', '1', true)")
    )

    delete_mode = bool(payload.get("tombstone")) or operation == "DELETE"

    pending = [dict(record) for record in records]
    results: list[dict[str, Any]] = []

    # Resolver dependencias FK dentro del evento en varias rondas.
    for _round in range(max(2, len(pending) + 1)):
        if not pending:
            break
        next_pending: list[dict[str, Any]] = []
        progress = False

        ordered = list(reversed(pending)) if delete_mode else list(pending)
        for record in ordered:
            result = _apply_record(
                session,
                empresa_id=empresa_id,
                origin_node_code=origin_node_code,
                record=record,
                delete_mode=delete_mode,
                origin_node_id=origin_node_id,
            )
            if result.get("status") == "DEFERRED":
                next_pending.append(record)
            else:
                results.append(result)
                progress = True

        pending = list(reversed(next_pending)) if delete_mode else next_pending
        if not progress:
            break

    if pending:
        unresolved = []
        for record in pending:
            unresolved.append(
                {
                    "table": record.get("table"),
                    "pk": record.get("pk"),
                }
            )
        raise RuntimeError(
            "Dependencias FK todavía no sincronizadas: "
            + _json_dumps(unresolved)
        )

    final_status = (
        "IGNORED"
        if results and all(r.get("status") == "IGNORED" for r in results)
        else "APPLIED"
    )

    session.connection().execute(
        sa_text(
            """
            UPDATE racknova_sync_inbox
            SET estado=:estado, ultimo_error=NULL, aplicado_en=NOW()
            WHERE id_evento=CAST(:id AS UUID)
            """
        ),
        {"id": event_id, "estado": final_status},
    )

    return {
        "id_evento": event_id,
        "status": final_status,
        "records": results,
    }


def _mark_inbox_error(
    session: Session,
    *,
    event: dict[str, Any],
    empresa_id: str,
    origin_node_code: str,
    error: str,
) -> None:
    try:
        event_id = str(UUID(str(event.get("id_evento"))))
    except Exception:
        return

    payload = event.get("payload") or {}
    session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_inbox (
                id_evento, empresa_id, origin_node_code,
                entidad, operacion, payload, estado,
                ultimo_error, recibido_en, aplicado_en
            )
            VALUES (
                CAST(:id AS UUID), CAST(:empresa AS UUID), :node,
                :entidad, :operacion, CAST(:payload AS JSONB), 'ERROR',
                :error, NOW(), NULL
            )
            ON CONFLICT (id_evento)
            DO UPDATE SET
                estado='ERROR',
                ultimo_error=EXCLUDED.ultimo_error,
                payload=EXCLUDED.payload
            """
        ),
        {
            "id": event_id,
            "empresa": str(empresa_id),
            "node": origin_node_code,
            "entidad": str(event.get("entidad") or ""),
            "operacion": str(event.get("operacion") or "EVENT"),
            "payload": _json_dumps(payload),
            "error": str(error)[:4000],
        },
    )


def ingest_batch(
    session: Session,
    *,
    body: SyncBatchIn,
) -> dict[str, Any]:
    ensure_sync_schema(session)

    if not body.events:
        return {"accepted": 0, "results": []}
    if len(body.events) > MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Máximo {MAX_BATCH} eventos por lote.",
        )

    company_ids = {str(event.empresa_id) for event in body.events}
    if len(company_ids) != 1:
        raise HTTPException(
            status_code=400,
            detail="Todos los eventos del lote deben pertenecer a la misma empresa.",
        )

    empresa_id = _validate_uuid(next(iter(company_ids)), "empresa_id")
    if not _company_exists(session, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada o inactiva.")

    _bind_empresa_system(session, empresa_id)
    node_id = _node_upsert_without_commit(
        session,
        empresa_id=empresa_id,
        code=body.origin_node_code,
        name=body.origin_node_name,
        node_type=body.origin_node_type,
        app_version=body.app_version,
    )

    results: list[dict[str, Any]] = []

    for model in body.events:
        event = model.model_dump()
        try:
            with session.begin_nested():
                result = _apply_event(
                    session,
                    empresa_id=empresa_id,
                    origin_node_code=body.origin_node_code,
                    origin_node_id=node_id,
                    event=event,
                )
            results.append(result)
        except Exception as exc:
            # El savepoint anterior revierte únicamente ese evento.
            _mark_inbox_error(
                session,
                event=event,
                empresa_id=empresa_id,
                origin_node_code=body.origin_node_code,
                error=str(exc),
            )
            results.append(
                {
                    "id_evento": event.get("id_evento"),
                    "status": "ERROR",
                    "error": str(exc),
                }
            )

    session.commit()
    return {
        "accepted": len(results),
        "empresa_id": empresa_id,
        "origin_node_code": body.origin_node_code,
        "results": results,
    }


def _http_json(
    *,
    method: str,
    url: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 15,
) -> Any:
    body = None
    headers = {
        "Accept": "application/json",
        "X-RackNova-Sync-Secret": secret,
        "User-Agent": "RackNova-Sync-B3/1",
    }
    if payload is not None:
        body = _json_dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url=url,
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Cloud HTTP {exc.code}: {raw[:1500]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"No se pudo contactar RackNova Cloud: {exc}") from exc


def _recover_stale_sending(session: Session, empresa_id: str) -> int:
    result = session.connection().execute(
        sa_text(
            f"""
            UPDATE racknova_sync_outbox
            SET estado='PENDING',
                ultimo_error='B3: recuperación automática de SENDING huérfano'
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND estado='SENDING'
              AND creado_en < NOW() - INTERVAL '{STALE_SENDING_MINUTES} minutes'
            """
        ),
        {"empresa": str(empresa_id)},
    )
    session.commit()
    return int(result.rowcount or 0)


def _claim_pending(
    session: Session,
    *,
    empresa_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT
                id_evento,
                empresa_id,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                creado_en
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND estado='PENDING'
              AND COALESCE((payload->>'schema_version')::integer, 0) >= :schema
            ORDER BY creado_en ASC, id_evento ASC
            FOR UPDATE SKIP LOCKED
            LIMIT :limite
            """
        ),
        {
            "empresa": str(empresa_id),
            "schema": B3_SCHEMA_VERSION,
            "limite": int(limit),
        },
    ).mappings().all()

    ids = [str(row["id_evento"]) for row in rows]
    if not ids:
        session.rollback()
        return []

    session.connection().execute(
        sa_text(
            """
            UPDATE racknova_sync_outbox
            SET estado='SENDING',
                intentos=intentos+1,
                ultimo_error=NULL,
                enviado_en=NOW()
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND id_evento = ANY(CAST(:ids AS UUID[]))
            """
        ),
        {"empresa": str(empresa_id), "ids": ids},
    )
    session.commit()
    return [dict(row) for row in rows]


def _finish_upload_result(
    session: Session,
    *,
    empresa_id: str,
    event_id: str,
    status: str,
    error: str | None,
) -> None:
    status = str(status or "").upper()

    if status in {"APPLIED", "DUPLICATE", "IGNORED"}:
        new_state = "SYNCED"
        error_value = None
    else:
        attempts = session.connection().execute(
            sa_text(
                """
                SELECT intentos
                FROM racknova_sync_outbox
                WHERE empresa_id=CAST(:empresa AS UUID)
                  AND id_evento=CAST(:id AS UUID)
                """
            ),
            {"empresa": str(empresa_id), "id": str(event_id)},
        ).scalar_one_or_none()
        new_state = "ERROR" if int(attempts or 0) >= MAX_ATTEMPTS else "PENDING"
        error_value = str(error or "Cloud rechazó el evento.")[:4000]

    session.connection().execute(
        sa_text(
            """
            UPDATE racknova_sync_outbox
            SET estado=:estado,
                ultimo_error=:error,
                enviado_en=CASE WHEN :estado='SYNCED' THEN NOW() ELSE NULL END
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND id_evento=CAST(:id AS UUID)
            """
        ),
        {
            "estado": new_state,
            "error": error_value,
            "empresa": str(empresa_id),
            "id": str(event_id),
        },
    )


def _sync_state_upsert(
    session: Session,
    *,
    empresa_id: str,
    node_id: str,
    subida: bool = False,
    bajada: bool = False,
    ultimo_evento: str | None = None,
    error: str | None = None,
) -> None:
    pending = session.connection().execute(
        sa_text(
            """
            SELECT COUNT(*)
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND estado='PENDING'
              AND COALESCE((payload->>'schema_version')::integer,0) >= :schema
            """
        ),
        {"empresa": str(empresa_id), "schema": B3_SCHEMA_VERSION},
    ).scalar_one()

    session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_estado (
                empresa_id, id_nodo,
                ultima_subida, ultima_bajada, ultimo_evento,
                pendiente_subir, ultimo_error, actualizado_en
            )
            VALUES (
                CAST(:empresa AS UUID), CAST(:node AS UUID),
                CASE WHEN :subida THEN NOW() ELSE NULL END,
                CASE WHEN :bajada THEN NOW() ELSE NULL END,
                CAST(:evento AS UUID),
                :pendiente, :error, NOW()
            )
            ON CONFLICT (id_nodo)
            DO UPDATE SET
                ultima_subida=CASE
                    WHEN :subida THEN NOW()
                    ELSE racknova_sync_estado.ultima_subida
                END,
                ultima_bajada=CASE
                    WHEN :bajada THEN NOW()
                    ELSE racknova_sync_estado.ultima_bajada
                END,
                ultimo_evento=COALESCE(
                    CAST(:evento AS UUID),
                    racknova_sync_estado.ultimo_evento
                ),
                pendiente_subir=:pendiente,
                ultimo_error=:error,
                actualizado_en=NOW()
            """
        ),
        {
            "empresa": str(empresa_id),
            "node": str(node_id),
            "subida": bool(subida),
            "bajada": bool(bajada),
            "evento": str(ultimo_evento) if ultimo_evento else None,
            "pendiente": int(pending or 0),
            "error": str(error)[:4000] if error else None,
        },
    )


def _cursor_get(
    session: Session,
    *,
    empresa_id: str,
    node_code: str,
) -> dict[str, Any]:
    row = session.connection().execute(
        sa_text(
            """
            SELECT last_created_at, last_event_id
            FROM racknova_sync_cursor
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND node_code=:node
              AND direccion='CLOUD_TO_LOCAL'
            LIMIT 1
            """
        ),
        {"empresa": str(empresa_id), "node": str(node_code)},
    ).mappings().first()
    return dict(row) if row else {}


def _cursor_put(
    session: Session,
    *,
    empresa_id: str,
    node_code: str,
    created_at: str | datetime | None,
    event_id: str | None,
) -> None:
    session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_cursor (
                empresa_id, node_code, direccion,
                last_created_at, last_event_id, actualizado_en
            )
            VALUES (
                CAST(:empresa AS UUID), :node, 'CLOUD_TO_LOCAL',
                CAST(:created_at AS TIMESTAMPTZ), CAST(:event_id AS UUID), NOW()
            )
            ON CONFLICT (empresa_id, node_code, direccion)
            DO UPDATE SET
                last_created_at=EXCLUDED.last_created_at,
                last_event_id=EXCLUDED.last_event_id,
                actualizado_en=NOW()
            """
        ),
        {
            "empresa": str(empresa_id),
            "node": str(node_code),
            "created_at": str(created_at) if created_at else None,
            "event_id": str(event_id) if event_id else None,
        },
    )


def pull_cloud_events(
    session: Session,
    *,
    empresa_id: str,
    after_created_at: str | None,
    after_event_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    ensure_sync_schema(session)
    empresa_id = _validate_uuid(empresa_id, "empresa_id")
    if not _company_exists(session, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    prefixes = list(PULL_ENTITY_PREFIXES)
    rows = session.connection().execute(
        sa_text(
            """
            SELECT
                id_evento,
                empresa_id,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                creado_en
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND COALESCE((payload->>'schema_version')::integer,0) >= :schema
              AND payload->>'runtime_mode'='cloud'
              AND (
                    entidad LIKE :p0
                 OR entidad LIKE :p1
                 OR entidad LIKE :p2
                 OR entidad LIKE :p3
              )
              AND (
                    :after_created IS NULL
                 OR creado_en > CAST(:after_created AS TIMESTAMPTZ)
                 OR (
                        creado_en = CAST(:after_created AS TIMESTAMPTZ)
                    AND id_evento > CAST(:after_event AS UUID)
                 )
              )
            ORDER BY creado_en ASC, id_evento ASC
            LIMIT :limite
            """
        ),
        {
            "empresa": empresa_id,
            "schema": B3_SCHEMA_VERSION,
            "p0": prefixes[0] + "%",
            "p1": prefixes[1] + "%",
            "p2": prefixes[2] + "%",
            "p3": prefixes[3] + "%",
            "after_created": after_created_at,
            "after_event": after_event_id or "00000000-0000-0000-0000-000000000000",
            "limite": int(limit),
        },
    ).mappings().all()
    return [dict(row) for row in rows]


def sync_once(
    session: Session,
    *,
    batch_limit: int = MAX_BATCH,
) -> dict[str, Any]:
    ensure_sync_schema(session)
    config = load_runtime_config()

    if not config.is_local:
        return {
            "mode": config.mode,
            "uploaded": 0,
            "downloaded": 0,
            "note": "El sender B3 solo se ejecuta en nodos RACKNOVA_MODE=local.",
        }

    if not config.empresa_id:
        raise RuntimeError("RACKNOVA_EMPRESA_ID es obligatorio en modo local.")

    cloud_url = _env("RACKNOVA_CLOUD_URL").rstrip("/")
    secret = _sync_secret()
    if not cloud_url:
        raise RuntimeError("RACKNOVA_CLOUD_URL no está configurado.")
    if not secret:
        raise RuntimeError("RACKNOVA_SYNC_SECRET no está configurado.")

    empresa_id = str(config.empresa_id)
    _bind_empresa_system(session, empresa_id)

    node = ensure_node_registered(
        session=session,
        empresa_id=empresa_id,
        config=config,
    )
    node_id = str(node["id_nodo"])

    _recover_stale_sending(session, empresa_id)

    claimed = _claim_pending(
        session,
        empresa_id=empresa_id,
        limit=min(MAX_BATCH, max(1, int(batch_limit))),
    )

    uploaded = 0
    upload_errors: list[dict[str, Any]] = []
    last_uploaded: str | None = None

    if claimed:
        payload = {
            "origin_node_code": config.node_code,
            "origin_node_name": config.node_name,
            "origin_node_type": config.node_type,
            "app_version": config.app_version,
            "events": [
                {
                    "id_evento": str(row["id_evento"]),
                    "empresa_id": str(row["empresa_id"]),
                    "entidad": row["entidad"],
                    "entidad_sync_uuid": (
                        str(row["entidad_sync_uuid"])
                        if row.get("entidad_sync_uuid")
                        else None
                    ),
                    "operacion": row["operacion"],
                    "payload": row["payload"],
                    "creado_en": str(row["creado_en"]) if row.get("creado_en") else None,
                }
                for row in claimed
            ],
        }

        try:
            response = _http_json(
                method="POST",
                url=cloud_url + "/sync/v1/ingest",
                secret=secret,
                payload=payload,
            )
            result_map = {
                str(item.get("id_evento")): item
                for item in list(response.get("results") or [])
            }
            for row in claimed:
                event_id = str(row["id_evento"])
                item = result_map.get(event_id) or {
                    "status": "ERROR",
                    "error": "Cloud no devolvió ACK para este evento.",
                }
                status = str(item.get("status") or "ERROR")
                _finish_upload_result(
                    session,
                    empresa_id=empresa_id,
                    event_id=event_id,
                    status=status,
                    error=item.get("error"),
                )
                if status.upper() in {"APPLIED", "DUPLICATE", "IGNORED"}:
                    uploaded += 1
                    last_uploaded = event_id
                else:
                    upload_errors.append(item)
            _sync_state_upsert(
                session,
                empresa_id=empresa_id,
                node_id=node_id,
                subida=uploaded > 0,
                ultimo_evento=last_uploaded,
                error=(
                    _json_dumps(upload_errors)[:4000]
                    if upload_errors else None
                ),
            )
            session.commit()
        except Exception as exc:
            for row in claimed:
                _finish_upload_result(
                    session,
                    empresa_id=empresa_id,
                    event_id=str(row["id_evento"]),
                    status="ERROR",
                    error=str(exc),
                )
            _sync_state_upsert(
                session,
                empresa_id=empresa_id,
                node_id=node_id,
                error=str(exc),
            )
            session.commit()
            upload_errors.append({"transport": str(exc)})

    # Cloud -> Local
    cursor = _cursor_get(
        session,
        empresa_id=empresa_id,
        node_code=config.node_code,
    )
    params = {
        "empresa_id": empresa_id,
        "limit": str(MAX_BATCH),
    }
    if cursor.get("last_created_at"):
        params["after_created_at"] = str(cursor["last_created_at"])
    if cursor.get("last_event_id"):
        params["after_event_id"] = str(cursor["last_event_id"])

    pull_url = cloud_url + "/sync/v1/pull?" + urllib.parse.urlencode(params)
    downloaded = 0
    pull_errors: list[dict[str, Any]] = []

    try:
        pulled = _http_json(
            method="GET",
            url=pull_url,
            secret=secret,
        )
        for event in list(pulled.get("events") or []):
            try:
                with session.begin_nested():
                    result = _apply_event(
                        session,
                        empresa_id=empresa_id,
                        origin_node_code="CLOUD",
                        origin_node_id=None,
                        event=event,
                    )
                if result.get("status") in {"APPLIED", "IGNORED", "DUPLICATE"}:
                    downloaded += 1
                    _cursor_put(
                        session,
                        empresa_id=empresa_id,
                        node_code=config.node_code,
                        created_at=event.get("creado_en"),
                        event_id=event.get("id_evento"),
                    )
                else:
                    pull_errors.append(result)
                    break
            except Exception as exc:
                pull_errors.append(
                    {
                        "id_evento": event.get("id_evento"),
                        "error": str(exc),
                    }
                )
                break

        _sync_state_upsert(
            session,
            empresa_id=empresa_id,
            node_id=node_id,
            bajada=downloaded > 0,
            error=(
                _json_dumps(pull_errors)[:4000]
                if pull_errors else None
            ),
        )
        session.commit()
    except Exception as exc:
        pull_errors.append({"transport": str(exc)})
        _sync_state_upsert(
            session,
            empresa_id=empresa_id,
            node_id=node_id,
            error=str(exc),
        )
        session.commit()

    return {
        "mode": config.mode,
        "empresa_id": empresa_id,
        "node_code": config.node_code,
        "uploaded": uploaded,
        "downloaded": downloaded,
        "upload_errors": upload_errors,
        "pull_errors": pull_errors,
    }


def sync_status(session: Session, *, empresa_id: str) -> dict[str, Any]:
    ensure_sync_schema(session)
    config = load_runtime_config()
    _bind_empresa_system(session, empresa_id)

    counts = session.connection().execute(
        sa_text(
            """
            SELECT
                estado,
                COUNT(*)::bigint AS total
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
            GROUP BY estado
            """
        ),
        {"empresa": str(empresa_id)},
    ).mappings().all()
    outbox_counts = {str(row["estado"]): int(row["total"] or 0) for row in counts}

    eligible = session.connection().execute(
        sa_text(
            """
            SELECT COUNT(*)
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND estado='PENDING'
              AND COALESCE((payload->>'schema_version')::integer,0) >= :schema
            """
        ),
        {"empresa": str(empresa_id), "schema": B3_SCHEMA_VERSION},
    ).scalar_one()

    legacy_pending = session.connection().execute(
        sa_text(
            """
            SELECT COUNT(*)
            FROM racknova_sync_outbox
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND estado='PENDING'
              AND COALESCE((payload->>'schema_version')::integer,0) < :schema
            """
        ),
        {"empresa": str(empresa_id), "schema": B3_SCHEMA_VERSION},
    ).scalar_one()

    inbox = session.connection().execute(
        sa_text(
            """
            SELECT estado, COUNT(*)::bigint AS total
            FROM racknova_sync_inbox
            WHERE empresa_id=CAST(:empresa AS UUID)
            GROUP BY estado
            """
        ),
        {"empresa": str(empresa_id)},
    ).mappings().all()

    state = session.connection().execute(
        sa_text(
            """
            SELECT
                n.codigo AS node_code,
                s.ultima_subida,
                s.ultima_bajada,
                s.ultimo_evento,
                s.pendiente_subir,
                s.ultimo_error,
                s.actualizado_en
            FROM racknova_sync_estado s
            JOIN racknova_nodos n ON n.id_nodo=s.id_nodo
            WHERE s.empresa_id=CAST(:empresa AS UUID)
            ORDER BY s.actualizado_en DESC
            LIMIT 10
            """
        ),
        {"empresa": str(empresa_id)},
    ).mappings().all()

    return {
        "fase": "2.5",
        "bloque": "B3_COMPLETO",
        "schema_version": B3_SCHEMA_VERSION,
        "runtime_mode": config.mode,
        "empresa_id": str(empresa_id),
        "transport": {
            "cloud_url_configured": bool(_env("RACKNOVA_CLOUD_URL")),
            "sync_secret_configured": bool(_sync_secret()),
            "autostart": _bool_env("RACKNOVA_SYNC_AUTOSTART", False),
            "interval_seconds": _int_env(
                "RACKNOVA_SYNC_INTERVAL_SECONDS",
                DEFAULT_INTERVAL_SECONDS,
                5,
                3600,
            ),
        },
        "outbox": {
            "counts": outbox_counts,
            "eligible_pending_schema4": int(eligible or 0),
            "legacy_pending_blocked": int(legacy_pending or 0),
        },
        "inbox": {
            str(row["estado"]): int(row["total"] or 0)
            for row in inbox
        },
        "nodes": [dict(row) for row in state],
        "worker_implemented": True,
        "cloud_receiver_implemented": True,
        "cloud_to_local_implemented": True,
    }


def quarantine_legacy_pending(
    session: Session,
    *,
    empresa_id: str,
) -> dict[str, Any]:
    ensure_sync_schema(session)
    result = session.connection().execute(
        sa_text(
            """
            UPDATE racknova_sync_outbox
            SET estado='ERROR',
                ultimo_error=(
                    'B3: evento pre-schema4 bloqueado para evitar '
                    'sincronización ambigua. Puede conservarse como histórico.'
                )
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND estado='PENDING'
              AND COALESCE((payload->>'schema_version')::integer,0) < :schema
            """
        ),
        {"empresa": str(empresa_id), "schema": B3_SCHEMA_VERSION},
    )
    session.commit()
    return {
        "empresa_id": str(empresa_id),
        "quarantined": int(result.rowcount or 0),
        "new_state": "ERROR",
    }


@contextmanager
def _session_from_dependency(get_session: Callable[..., Any]):
    provider = get_session()
    session = None
    try:
        if hasattr(provider, "__next__"):
            session = next(provider)
        else:
            session = provider
        if session is None:
            raise RuntimeError("get_session no devolvió una sesión.")
        yield session
    finally:
        try:
            if hasattr(provider, "close"):
                provider.close()
        except Exception:
            pass
        try:
            if session is not None and hasattr(session, "close"):
                session.close()
        except Exception:
            pass


def start_background_worker(get_session: Callable[..., Any]) -> bool:
    global _WORKER_STARTED
    config = load_runtime_config()
    if not config.is_local or not _bool_env("RACKNOVA_SYNC_AUTOSTART", False):
        return False

    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return True
        _WORKER_STARTED = True

        interval = _int_env(
            "RACKNOVA_SYNC_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
            5,
            3600,
        )

        def loop() -> None:
            # Esperar a que la aplicación termine de levantar.
            time.sleep(3)
            while True:
                try:
                    with _session_from_dependency(get_session) as session:
                        sync_once(session)
                except Exception as exc:
                    print(f"[RackNova Sync B3] worker error: {exc}")
                time.sleep(interval)

        thread = threading.Thread(
            target=loop,
            name="racknova-sync-b3",
            daemon=True,
        )
        thread.start()
        return True


def register_sync_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    @app.get("/sync/v1/status", tags=["RackNova Sync B3"])
    def racknova_sync_b3_status(
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
        return sync_status(
            session,
            empresa_id=str(selected["id_empresa"]),
        )

    @app.post("/sync/v1/bootstrap", tags=["RackNova Sync B3"])
    def racknova_sync_b3_bootstrap(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin"},
        )
        ensure_sync_schema(session)
        return {
            "ok": True,
            "empresa_id": str(selected["id_empresa"]),
            "schema_version": B3_SCHEMA_VERSION,
            "message": "Infraestructura RackNova Sync B3 lista.",
        }

    @app.post("/sync/v1/run", tags=["RackNova Sync B3"])
    def racknova_sync_b3_run(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin"},
        )
        config = load_runtime_config()
        if config.is_local and str(config.empresa_id or "") != str(selected["id_empresa"]):
            raise HTTPException(
                status_code=403,
                detail="El nodo local está ligado a otra empresa.",
            )
        return sync_once(session)

    @app.post("/sync/v1/quarantine-legacy", tags=["RackNova Sync B3"])
    def racknova_sync_b3_quarantine(
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin"},
        )
        return quarantine_legacy_pending(
            session,
            empresa_id=str(selected["id_empresa"]),
        )

    @app.get("/sync/v1/inbox", tags=["RackNova Sync B3"])
    def racknova_sync_b3_inbox(
        estado: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            rn_empresa_id,
            allowed_roles={"owner", "admin"},
        )
        ensure_sync_schema(session)
        rows = session.connection().execute(
            sa_text(
                """
                SELECT
                    id_evento, empresa_id, origin_node_code,
                    entidad, operacion, estado, ultimo_error,
                    recibido_en, aplicado_en
                FROM racknova_sync_inbox
                WHERE empresa_id=CAST(:empresa AS UUID)
                  AND (:estado IS NULL OR estado=:estado)
                ORDER BY recibido_en DESC
                LIMIT :limit
                """
            ),
            {
                "empresa": str(selected["id_empresa"]),
                "estado": str(estado or "").upper() or None,
                "limit": int(limit),
            },
        ).mappings().all()
        return [dict(row) for row in rows]

    # RackNova IA Local -> Cloud. La API key del proveedor nunca sale de Cloud.
    @app.post("/sync/v1/ai/complete", tags=["RackNova IA"])
    def racknova_sync_ai_complete(
        body: RackNovaAICloudCompletionRequest,
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
                detail="El relay de RackNova IA solo está disponible en modo cloud.",
            )

        empresa_id = _validate_uuid(body.empresa_id, "empresa_id")
        if not _company_exists(session, empresa_id):
            raise HTTPException(
                status_code=404,
                detail="Empresa no encontrada o inactiva.",
            )

        node_exists = session.connection().execute(
            sa_text(
                """
                SELECT 1
                FROM racknova_nodos
                WHERE empresa_id=CAST(:empresa AS UUID)
                  AND codigo=:codigo
                  AND activo=TRUE
                LIMIT 1
                """
            ),
            {
                "empresa": empresa_id,
                "codigo": str(body.origin_node_code),
            },
        ).scalar_one_or_none()
        if not node_exists:
            raise HTTPException(
                status_code=403,
                detail="El nodo local no está registrado o está inactivo.",
            )

        return request_deepseek_from_cloud(
            messages=[item.model_dump() for item in body.messages],
            max_tokens=body.max_tokens,
            user_id=body.user_id,
        )

    # Endpoint máquina-a-máquina. No usa login de usuario.
    @app.post("/sync/v1/ingest", tags=["RackNova Sync B3"])
    def racknova_sync_b3_ingest(
        body: SyncBatchIn,
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
                detail="El receptor /sync/v1/ingest solo acepta eventos en modo cloud.",
            )
        return ingest_batch(session, body=body)

    @app.get("/sync/v1/pull", tags=["RackNova Sync B3"])
    def racknova_sync_b3_pull(
        empresa_id: str,
        limit: int = Query(default=25, ge=1, le=100),
        after_created_at: str | None = Query(default=None),
        after_event_id: str | None = Query(default=None),
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
                detail="/sync/v1/pull solo sirve configuración desde RackNova Cloud.",
            )
        events = pull_cloud_events(
            session,
            empresa_id=empresa_id,
            after_created_at=after_created_at,
            after_event_id=after_event_id,
            limit=limit,
        )
        return {
            "empresa_id": empresa_id,
            "events": events,
            "count": len(events),
            "server_time": _utcnow_iso(),
        }

    # El autostart solo hace algo en RACKNOVA_MODE=local.
    try:
        @app.on_event("startup")
        def _racknova_sync_b3_startup() -> None:
            start_background_worker(get_session)
    except Exception:
        # Si una versión futura de FastAPI elimina on_event, el endpoint
        # /sync/v1/run seguirá permitiendo ejecución manual.
        pass
