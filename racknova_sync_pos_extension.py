from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Any

from sqlalchemy import text as sa_text
from sqlmodel import Session

import racknova_sync_worker as worker

_INSTALLED = False
_ORIGINAL_SYNC_ONCE = worker.sync_once

POS_PULL_PREFIX = "pos."
POS_BACKFILL_PAGE_SIZE = 100
POS_BACKFILL_MAX_PAGES_PER_RUN = 3


def _pull_cloud_events_with_pos(
    session: Session,
    *,
    empresa_id: str,
    after_created_at: str | None,
    after_event_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """
    Amplía el feed Cloud -> Local para incluir operaciones POS.

    Conserva los cuatro prefijos existentes y agrega pos.*. La aplicación de
    cada payload sigue usando el motor B3 original (idempotencia, revisión,
    mapeo de PK y resolución de FK).
    """
    worker.ensure_sync_schema(session)
    empresa_id = worker._validate_uuid(empresa_id, "empresa_id")
    if not worker._company_exists(session, empresa_id):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    prefixes = (
        "config.",
        "catalog.",
        "customer.",
        "inventory.",
        POS_PULL_PREFIX,
    )
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
                 OR entidad LIKE :p4
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
            "schema": worker.B3_SCHEMA_VERSION,
            "p0": prefixes[0] + "%",
            "p1": prefixes[1] + "%",
            "p2": prefixes[2] + "%",
            "p3": prefixes[3] + "%",
            "p4": prefixes[4] + "%",
            "after_created": after_created_at,
            "after_event": after_event_id
            or "00000000-0000-0000-0000-000000000000",
            "limite": int(limit),
        },
    ).mappings().all()
    return [dict(row) for row in rows]


def _ensure_pos_cursor(session: Session) -> None:
    session.connection().execute(
        sa_text(
            """
            CREATE TABLE IF NOT EXISTS racknova_sync_pos_cursor (
                empresa_id UUID NOT NULL REFERENCES empresas(id_empresa) ON DELETE CASCADE,
                node_code VARCHAR(120) NOT NULL,
                last_created_at TIMESTAMPTZ,
                last_event_id UUID,
                actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (empresa_id, node_code)
            )
            """
        )
    )
    session.commit()


def _pos_cursor_get(
    session: Session,
    *,
    empresa_id: str,
    node_code: str,
) -> dict[str, Any]:
    _ensure_pos_cursor(session)
    row = session.connection().execute(
        sa_text(
            """
            SELECT last_created_at, last_event_id
            FROM racknova_sync_pos_cursor
            WHERE empresa_id=CAST(:empresa AS UUID)
              AND node_code=:node
            LIMIT 1
            """
        ),
        {"empresa": str(empresa_id), "node": str(node_code)},
    ).mappings().first()
    return dict(row) if row else {}


def _pos_cursor_put(
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
            INSERT INTO racknova_sync_pos_cursor (
                empresa_id, node_code,
                last_created_at, last_event_id, actualizado_en
            )
            VALUES (
                CAST(:empresa AS UUID), :node,
                CAST(:created_at AS TIMESTAMPTZ), CAST(:event_id AS UUID), NOW()
            )
            ON CONFLICT (empresa_id, node_code)
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


def _pos_entity(event: dict[str, Any]) -> bool:
    payload = dict(event.get("payload") or {})
    entity = str(event.get("entidad") or payload.get("event_type") or "")
    return entity.startswith(POS_PULL_PREFIX)


def _sync_pos_backfill_once(session: Session) -> dict[str, Any]:
    """
    Cursor independiente para recuperar POS Cloud histórico.

    Avanza también sobre eventos no POS del feed para no resetear ni tocar el
    cursor B3 principal. Solo aplica pos.*. Si un evento POS falla por una FK
    todavía ausente, el cursor queda antes de ese evento y se reintenta luego.
    """
    config = worker.load_runtime_config()
    if not config.is_local or not config.empresa_id:
        return {"scanned": 0, "pos_applied": 0, "errors": []}

    cloud_url = worker._env("RACKNOVA_CLOUD_URL").rstrip("/")
    secret = worker._sync_secret()
    if not cloud_url or not secret:
        return {"scanned": 0, "pos_applied": 0, "errors": []}

    empresa_id = str(config.empresa_id)
    worker._bind_empresa_system(session, empresa_id)
    cursor = _pos_cursor_get(
        session,
        empresa_id=empresa_id,
        node_code=config.node_code,
    )

    scanned = 0
    pos_applied = 0
    errors: list[dict[str, Any]] = []

    for _page in range(POS_BACKFILL_MAX_PAGES_PER_RUN):
        params = {
            "empresa_id": empresa_id,
            "limit": str(POS_BACKFILL_PAGE_SIZE),
        }
        if cursor.get("last_created_at"):
            params["after_created_at"] = str(cursor["last_created_at"])
        if cursor.get("last_event_id"):
            params["after_event_id"] = str(cursor["last_event_id"])

        pull_url = cloud_url + "/sync/v1/pull?" + urllib.parse.urlencode(params)
        pulled = worker._http_json(
            method="GET",
            url=pull_url,
            secret=secret,
        )
        events = list(pulled.get("events") or [])
        if not events:
            break

        blocked = False
        for event in events:
            scanned += 1
            if _pos_entity(event):
                try:
                    with session.begin_nested():
                        result = worker._apply_event(
                            session,
                            empresa_id=empresa_id,
                            origin_node_code="CLOUD",
                            origin_node_id=None,
                            event=event,
                        )
                    if result.get("status") not in {
                        "APPLIED",
                        "IGNORED",
                        "DUPLICATE",
                    }:
                        errors.append(result)
                        blocked = True
                        break
                    pos_applied += 1
                except Exception as exc:
                    errors.append(
                        {
                            "id_evento": event.get("id_evento"),
                            "entidad": event.get("entidad"),
                            "error": str(exc),
                        }
                    )
                    blocked = True
                    break

            _pos_cursor_put(
                session,
                empresa_id=empresa_id,
                node_code=config.node_code,
                created_at=event.get("creado_en"),
                event_id=event.get("id_evento"),
            )
            cursor = {
                "last_created_at": event.get("creado_en"),
                "last_event_id": event.get("id_evento"),
            }

        session.commit()
        if blocked or len(events) < POS_BACKFILL_PAGE_SIZE:
            break

    return {
        "scanned": scanned,
        "pos_applied": pos_applied,
        "errors": errors,
        "cursor": cursor,
    }


def _sync_once_with_pos(
    session: Session,
    *,
    batch_limit: int = worker.MAX_BATCH,
) -> dict[str, Any]:
    result = _ORIGINAL_SYNC_ONCE(session, batch_limit=batch_limit)

    try:
        pos_result = _sync_pos_backfill_once(session)
    except Exception as exc:
        pos_result = {
            "scanned": 0,
            "pos_applied": 0,
            "errors": [{"transport": str(exc)}],
        }

    if isinstance(result, dict):
        result["pos_cloud_to_local"] = pos_result
    return result


def install_pos_cloud_to_local_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    worker.PULL_ENTITY_PREFIXES = (
        "config.",
        "catalog.",
        "customer.",
        "inventory.",
        POS_PULL_PREFIX,
    )
    worker.pull_cloud_events = _pull_cloud_events_with_pos
    worker.sync_once = _sync_once_with_pos
    _INSTALLED = True
