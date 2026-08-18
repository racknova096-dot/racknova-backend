# ============================================================
# RACKNOVA FASE 2.5 - BLOQUE B2A
# Captura transaccional de operaciones reales -> Durable Outbox
# ============================================================
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sa_text
from sqlmodel import Session

import multiempresa_tenant as rn_tenant
from racknova_outbox import deterministic_event_id, enqueue_outbox_event
from racknova_runtime import load_runtime_config


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
}

SENSITIVE_TOKENS = (
    "password",
    "contrasena",
    "contraseña",
    "secret",
    "access_token",
    "refresh_token",
    "authorization",
    "cvv",
    "cvc",
)

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_sensitive(name: str) -> bool:
    low = str(name or "").lower()
    return any(token in low for token in SENSITIVE_TOKENS)


def _json_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (UUID, Decimal, datetime, date)):
        return str(value)

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            key_s = str(key)
            if _is_sensitive(key_s):
                continue
            result[key_s] = _json_value(item, depth + 1)
        return result

    if isinstance(value, (list, tuple, set)):
        return [_json_value(v, depth + 1) for v in list(value)[:100]]

    # Pydantic v2/v1, pero no serializamos secretos por nombre de campo.
    dump = None
    if hasattr(value, "model_dump"):
        try:
            dump = value.model_dump()
        except Exception:
            dump = None
    elif hasattr(value, "dict"):
        try:
            dump = value.dict()
        except Exception:
            dump = None

    if isinstance(dump, dict):
        return _json_value(dump, depth + 1)

    return str(value)


def _mapper_for(obj: Any):
    try:
        inspected = sa_inspect(obj)
        mapper = inspected.mapper
        return mapper
    except Exception:
        return None


def _table_name(obj: Any) -> str | None:
    mapper = _mapper_for(obj)
    if mapper is None:
        return None
    try:
        return str(mapper.local_table.name)
    except Exception:
        return None


def _is_commercial_object(obj: Any) -> bool:
    table = (_table_name(obj) or "").lower()
    if not table or table in EXCLUDED_TABLES:
        return False
    return table.startswith(COMMERCIAL_PREFIXES)


def _pk_values(obj: Any) -> dict[str, Any]:
    mapper = _mapper_for(obj)
    if mapper is None:
        return {}
    result: dict[str, Any] = {}
    for col in mapper.primary_key:
        try:
            result[col.name] = getattr(obj, col.key)
        except Exception:
            result[col.name] = None
    return result


def _read_sync_uuid(
    session: Session,
    *,
    table: str,
    empresa_id: str,
    pk: dict[str, Any],
) -> str | None:
    if not SAFE_IDENTIFIER.match(table):
        return None
    if not pk or any(v is None for v in pk.values()):
        return None
    if any(not SAFE_IDENTIFIER.match(k) for k in pk):
        return None

    where = [
        f'"{key}" = :pk_{i}'
        for i, key in enumerate(pk)
    ]
    params = {
        f"pk_{i}": value
        for i, value in enumerate(pk.values())
    }
    params["empresa_id"] = str(empresa_id)

    sql = (
        f'SELECT sync_uuid FROM "{table}" '
        f'WHERE empresa_id = CAST(:empresa_id AS UUID) '
        f'AND {" AND ".join(where)} LIMIT 1'
    )

    try:
        value = session.connection().execute(
            sa_text(sql),
            params,
        ).scalar_one_or_none()
        return str(value) if value else None
    except Exception:
        return None


def _serialize_record(
    session: Session,
    *,
    obj: Any,
    empresa_id: str,
) -> dict[str, Any] | None:
    mapper = _mapper_for(obj)
    table = _table_name(obj)
    if mapper is None or not table or not _is_commercial_object(obj):
        return None

    data: dict[str, Any] = {}
    for col in mapper.columns:
        name = str(col.key)
        if _is_sensitive(name):
            continue
        try:
            data[name] = _json_value(getattr(obj, name))
        except Exception:
            continue

    pk = _pk_values(obj)
    sync_uuid = _read_sync_uuid(
        session,
        table=table,
        empresa_id=empresa_id,
        pk=pk,
    )

    return {
        "table": table,
        "pk": _json_value(pk),
        "sync_uuid": sync_uuid,
        "data": data,
    }


def _walk_mapped(value: Any, found: dict[int, Any], depth: int = 0) -> None:
    if depth > 4 or value is None:
        return

    if _mapper_for(value) is not None:
        found[id(value)] = value
        return

    if isinstance(value, dict):
        for key, item in list(value.items())[:100]:
            if _is_sensitive(str(key)):
                continue
            _walk_mapped(item, found, depth + 1)
        return

    if isinstance(value, (list, tuple, set)):
        for item in list(value)[:200]:
            _walk_mapped(item, found, depth + 1)


def _safe_context(local_vars: dict[str, Any]) -> dict[str, Any]:
    """
    Guarda únicamente identificadores/primitivos útiles. Los objetos ORM
    completos se incluyen por separado en records.
    """
    result: dict[str, Any] = {}
    skip_names = {
        "session",
        "db",
        "current_user",
        "user",
        "usuario",
        "usuario_actual",
    }

    for key, value in local_vars.items():
        if key in skip_names or _is_sensitive(key):
            continue
        if key.startswith("_"):
            continue

        if value is None or isinstance(
            value,
            (str, int, float, bool, UUID, Decimal, datetime, date),
        ):
            result[key] = _json_value(value)
            continue

        # De request/Pydantic solo tomamos campos seguros si el objeto es pequeño.
        if hasattr(value, "model_dump") or hasattr(value, "dict"):
            dumped = _json_value(value)
            try:
                encoded = json.dumps(dumped, default=str)
            except Exception:
                encoded = ""
            if len(encoded) <= 20_000:
                result[key] = dumped

    return result


def _stable_token(
    *,
    event_type: str,
    records: list[dict[str, Any]],
    context: dict[str, Any],
) -> str | None:
    # 1) Operacion ID: ideal para crear ventas.
    for record in records:
        data = record.get("data") or {}
        value = data.get("operacion_id")
        if value:
            return f"operacion_id:{value}"

    for key in ("operacion_id", "operation_id"):
        value = context.get(key)
        if value:
            return f"{key}:{value}"

    # 2) Para entidades nuevas especializadas usamos su sync_uuid.
    priorities = ("devol", "abono", "movimiento", "sesion", "venta")
    for token in priorities:
        for record in records:
            if token in str(record.get("table", "")).lower():
                sync_uuid = record.get("sync_uuid")
                if sync_uuid:
                    return f"{token}:sync:{sync_uuid}"

    # 3) Si hay un único registro nuevo/relevante con UUID estable.
    sync_ids = sorted(
        {
            str(record["sync_uuid"])
            for record in records
            if record.get("sync_uuid")
        }
    )
    if len(sync_ids) == 1:
        return f"sync:{sync_ids[0]}"

    return None


def _upsert_node_without_commit(
    session: Session,
    *,
    empresa_id: str,
) -> str | None:
    config = load_runtime_config()

    try:
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
                RETURNING id_nodo
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
        return str(row["id_nodo"]) if row else None
    except Exception:
        # id_nodo es nullable en outbox. No rompemos una venta solo porque
        # no se pudo refrescar el registro del nodo.
        return None


def capture_operation_event(
    session: Session,
    *,
    event_type: str,
    local_vars: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Se llama INMEDIATAMENTE ANTES del commit comercial.

    1. captura objetos nuevos/sucios/borrados;
    2. hace flush, NO commit;
    3. obtiene PK/sync_uuid;
    4. inserta el evento outbox en la MISMA transacción;
    5. devuelve al caller, que realiza el commit original.
    """
    local_vars = dict(local_vars or {})
    empresa_id = rn_tenant.current_empresa_id(session)
    config = load_runtime_config()

    mapped: dict[int, Any] = {}

    # Cambios todavía pendientes.
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if _is_commercial_object(obj):
            mapped[id(obj)] = obj

    # Objetos que ya fueron flushados antes dentro de la misma ruta.
    for value in local_vars.values():
        _walk_mapped(value, mapped)

    # Fundamental: asigna PKs y defaults de DB antes de consultar sync_uuid.
    session.flush()

    records: list[dict[str, Any]] = []
    for obj in list(mapped.values())[:250]:
        record = _serialize_record(
            session,
            obj=obj,
            empresa_id=str(empresa_id),
        )
        if record:
            records.append(record)

    context = _safe_context(local_vars)
    stable = _stable_token(
        event_type=event_type,
        records=records,
        context=context,
    )

    event_id = (
        deterministic_event_id(
            empresa_id=str(empresa_id),
            node_code=config.node_code,
            entity=event_type,
            operation="EVENT",
            token=f"{event_type}:{stable}",
        )
        if stable
        else uuid4()
    )

    node_id = _upsert_node_without_commit(
        session,
        empresa_id=str(empresa_id),
    )

    payload = {
        "schema_version": 1,
        "event_type": event_type,
        "runtime_mode": config.mode,
        "node_code": config.node_code,
        "empresa_id": str(empresa_id),
        "context": context,
        "records": records,
        "record_count": len(records),
        "capture": "transactional-before-commit",
    }

    return enqueue_outbox_event(
        session,
        empresa_id=str(empresa_id),
        node_id=node_id,
        entity=event_type,
        operation="EVENT",
        payload=payload,
        event_id=event_id,
    )
