# ============================================================
# RACKNOVA FASE 2.5 - BLOQUE B1
# Outbox durable e idempotente para Local <-> Cloud
# ============================================================
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4, uuid5

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlmodel import Session

import multiempresa_tenant as rn_tenant
from racknova_runtime import (
    ensure_node_registered,
    load_runtime_config,
)


# Namespace estable propio de RackNova para UUID5 de eventos deterministas.
RACKNOVA_EVENT_NAMESPACE = UUID("994ecdf6-9ba1-4e76-96a6-efc9d652dd0f")

VALID_OPERATIONS = {"INSERT", "UPDATE", "DELETE", "EVENT"}
VALID_STATES = {"PENDING", "SENDING", "SYNCED", "ERROR"}


class DiagnosticEventRequest(BaseModel):
    token: str | None = Field(
        default=None,
        description=(
            "Token opcional. Si repites el mismo token en la misma empresa/nodo, "
            "se reutiliza el mismo id_evento y no se duplica."
        ),
        max_length=200,
    )
    note: str | None = Field(default=None, max_length=500)


def _json_payload(value: Any) -> str:
    import json
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def deterministic_event_id(
    *,
    empresa_id: str,
    node_code: str,
    entity: str,
    operation: str,
    entity_sync_uuid: str | None = None,
    revision: int | None = None,
    token: str | None = None,
) -> UUID:
    """
    Genera un UUID estable cuando existe información suficiente para que
    un reintento lógico produzca exactamente el mismo evento.

    Para eventos sin token/revisión, el caller debe usar uuid4().
    """
    parts = [
        str(empresa_id),
        str(node_code).strip().upper(),
        str(entity).strip().lower(),
        str(operation).strip().upper(),
        str(entity_sync_uuid or ""),
        str(revision if revision is not None else ""),
        str(token or ""),
    ]
    return uuid5(RACKNOVA_EVENT_NAMESPACE, "|".join(parts))


def enqueue_outbox_event(
    session: Session,
    *,
    empresa_id: str,
    node_id: str | None,
    entity: str,
    operation: str,
    payload: dict[str, Any] | None = None,
    entity_sync_uuid: str | None = None,
    event_id: str | UUID | None = None,
) -> dict[str, Any]:
    """
    Inserta un evento SIN hacer commit.

    Esto es intencional: cuando conectemos ventas/inventario, el cambio
    comercial y el evento outbox deben confirmarse en la MISMA transacción.

    Si se suministra el mismo event_id dos veces, ON CONFLICT evita duplicarlo.
    """
    entity = str(entity or "").strip()
    operation = str(operation or "").strip().upper()

    if not entity:
        raise ValueError("entity es obligatorio.")

    if operation not in VALID_OPERATIONS:
        raise ValueError(
            f"operation debe ser una de: {', '.join(sorted(VALID_OPERATIONS))}"
        )

    if event_id is None:
        event_uuid = uuid4()
    else:
        event_uuid = UUID(str(event_id))

    entity_uuid = None
    if entity_sync_uuid:
        entity_uuid = UUID(str(entity_sync_uuid))

    row = session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_outbox (
                id_evento,
                empresa_id,
                id_nodo,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                estado,
                intentos,
                ultimo_error,
                creado_en,
                enviado_en
            )
            VALUES (
                CAST(:id_evento AS UUID),
                CAST(:empresa_id AS UUID),
                CAST(:id_nodo AS UUID),
                :entidad,
                CAST(:entidad_sync_uuid AS UUID),
                :operacion,
                CAST(:payload AS JSONB),
                'PENDING',
                0,
                NULL,
                NOW(),
                NULL
            )
            ON CONFLICT (id_evento)
            DO NOTHING
            RETURNING
                id_evento,
                empresa_id,
                id_nodo,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                estado,
                intentos,
                ultimo_error,
                creado_en,
                enviado_en
            """
        ),
        {
            "id_evento": str(event_uuid),
            "empresa_id": str(empresa_id),
            "id_nodo": str(node_id) if node_id else None,
            "entidad": entity,
            "entidad_sync_uuid": str(entity_uuid) if entity_uuid else None,
            "operacion": operation,
            "payload": _json_payload(payload or {}),
        },
    ).mappings().first()

    if row:
        result = dict(row)
        result["inserted"] = True
        return result

    # Ya existía: devolver el evento actual, siempre validando empresa.
    existing = session.connection().execute(
        sa_text(
            """
            SELECT
                id_evento,
                empresa_id,
                id_nodo,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                estado,
                intentos,
                ultimo_error,
                creado_en,
                enviado_en
            FROM racknova_sync_outbox
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND id_evento = CAST(:id_evento AS UUID)
            LIMIT 1
            """
        ),
        {
            "empresa_id": str(empresa_id),
            "id_evento": str(event_uuid),
        },
    ).mappings().first()

    if not existing:
        raise HTTPException(
            status_code=409,
            detail="El id_evento ya existe fuera del contexto esperado.",
        )

    result = dict(existing)
    result["inserted"] = False
    return result


def _current_node(
    session: Session,
    *,
    empresa_id: str,
) -> dict[str, Any]:
    """
    Garantiza que el nodo del runtime exista para la empresa actual.
    El upsert también actualiza ultima_conexion/version_app.
    """
    config = load_runtime_config()
    return ensure_node_registered(
        session=session,
        empresa_id=str(empresa_id),
        config=config,
    )


def outbox_status(
    session: Session,
    *,
    empresa_id: str,
) -> dict[str, Any]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT estado, COUNT(*)::bigint AS total
            FROM racknova_sync_outbox
            WHERE empresa_id = CAST(:empresa_id AS UUID)
            GROUP BY estado
            """
        ),
        {"empresa_id": str(empresa_id)},
    ).mappings().all()

    counts = {state: 0 for state in sorted(VALID_STATES)}
    for row in rows:
        state = str(row["estado"])
        if state in counts:
            counts[state] = int(row["total"] or 0)

    total = sum(counts.values())

    oldest = session.connection().execute(
        sa_text(
            """
            SELECT MIN(creado_en)
            FROM racknova_sync_outbox
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND estado = 'PENDING'
            """
        ),
        {"empresa_id": str(empresa_id)},
    ).scalar_one_or_none()

    last_event = session.connection().execute(
        sa_text(
            """
            SELECT creado_en
            FROM racknova_sync_outbox
            WHERE empresa_id = CAST(:empresa_id AS UUID)
            ORDER BY creado_en DESC
            LIMIT 1
            """
        ),
        {"empresa_id": str(empresa_id)},
    ).scalar_one_or_none()

    return {
        "empresa_id": str(empresa_id),
        "counts": counts,
        "total": total,
        "oldest_pending_at": oldest,
        "last_event_at": last_event,
        "sync_worker_implemented": True,
    }


def list_outbox(
    session: Session,
    *,
    empresa_id: str,
    state: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    state = (state or "").strip().upper() or None
    if state and state not in VALID_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"estado debe ser uno de: {', '.join(sorted(VALID_STATES))}",
        )

    rows = session.connection().execute(
        sa_text(
            """
            SELECT
                id_evento,
                empresa_id,
                id_nodo,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                estado,
                intentos,
                ultimo_error,
                creado_en,
                enviado_en
            FROM racknova_sync_outbox
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND (:estado IS NULL OR estado = :estado)
            ORDER BY creado_en DESC
            LIMIT :limite
            """
        ),
        {
            "empresa_id": str(empresa_id),
            "estado": state,
            "limite": int(limit),
        },
    ).mappings().all()

    return [dict(row) for row in rows]


def reset_event_for_retry(
    session: Session,
    *,
    empresa_id: str,
    event_id: str,
) -> dict[str, Any]:
    try:
        event_uuid = UUID(str(event_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="id_evento no es UUID válido.",
        ) from exc

    row = session.connection().execute(
        sa_text(
            """
            UPDATE racknova_sync_outbox
            SET
                estado = 'PENDING',
                ultimo_error = NULL,
                enviado_en = NULL
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND id_evento = CAST(:id_evento AS UUID)
              AND estado IN ('ERROR', 'SENDING')
            RETURNING
                id_evento,
                empresa_id,
                id_nodo,
                entidad,
                entidad_sync_uuid,
                operacion,
                payload,
                estado,
                intentos,
                ultimo_error,
                creado_en,
                enviado_en
            """
        ),
        {
            "empresa_id": str(empresa_id),
            "id_evento": str(event_uuid),
        },
    ).mappings().first()

    if row:
        session.commit()
        return dict(row)

    existing = session.connection().execute(
        sa_text(
            """
            SELECT estado
            FROM racknova_sync_outbox
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND id_evento = CAST(:id_evento AS UUID)
            LIMIT 1
            """
        ),
        {
            "empresa_id": str(empresa_id),
            "id_evento": str(event_uuid),
        },
    ).mappings().first()

    if not existing:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")

    raise HTTPException(
        status_code=409,
        detail=f"El evento está en estado {existing['estado']} y no requiere retry manual.",
    )


def register_outbox_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    @app.get(
        "/sync/outbox/status",
        tags=["RackNova Sync"],
    )
    def racknova_outbox_status(
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
        return outbox_status(
            session,
            empresa_id=str(selected["id_empresa"]),
        )

    @app.get(
        "/sync/outbox",
        tags=["RackNova Sync"],
    )
    def racknova_outbox_list(
        estado: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
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
        return list_outbox(
            session,
            empresa_id=str(selected["id_empresa"]),
            state=estado,
            limit=limit,
        )

    @app.post(
        "/sync/outbox/diagnostic-event",
        tags=["RackNova Sync"],
    )
    def racknova_outbox_diagnostic_event(
        body: DiagnosticEventRequest,
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

        node = _current_node(
            session,
            empresa_id=empresa_id,
        )
        config = load_runtime_config()

        token = (body.token or "").strip() or str(uuid4())
        event_id = deterministic_event_id(
            empresa_id=empresa_id,
            node_code=config.node_code,
            entity="racknova_diagnostic",
            operation="EVENT",
            token=token,
        )

        event = enqueue_outbox_event(
            session,
            empresa_id=empresa_id,
            node_id=str(node["id_nodo"]),
            entity="racknova_diagnostic",
            operation="EVENT",
            payload={
                "token": token,
                "note": body.note,
                "runtime_mode": config.mode,
                "node_code": config.node_code,
                "purpose": "Fase 2.5 Bloque B1 diagnostic",
            },
            event_id=event_id,
        )
        session.commit()

        return {
            "mensaje": (
                "Evento diagnóstico creado."
                if event["inserted"]
                else "El mismo evento diagnóstico ya existía; no se duplicó."
            ),
            "idempotent": True,
            "event": event,
        }

    @app.post(
        "/sync/outbox/{event_id}/retry",
        tags=["RackNova Sync"],
    )
    def racknova_outbox_retry(
        event_id: str,
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
        return reset_event_for_retry(
            session,
            empresa_id=str(selected["id_empresa"]),
            event_id=event_id,
        )
