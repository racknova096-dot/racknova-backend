from __future__ import annotations

"""RackNova Multiempresa — Fase 2 LOCAL-FIRST READY.

Aislamiento real por empresa para SQLModel/SQLAlchemy, separación entre
administradores de plataforma y roles internos del cliente, y compatibilidad
con la futura arquitectura RackNova Local <-> RackNova Cloud.

X-Empresa-ID nunca se confía directamente: el backend valida membresía y rol.
"""

from typing import Any, Iterable, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import event, text as sa_text
from sqlalchemy.orm import Session as SASession

DEFAULT_EMPRESA_ID = "11111111-1111-4111-8111-111111111111"
SESSION_EMPRESA_KEY = "racknova_empresa_id"
SESSION_ROL_KEY = "racknova_empresa_role"
_EVENTS_INSTALLED = False


def _get(user: Any, name: str) -> Any:
    if isinstance(user, dict):
        return user.get(name)
    return getattr(user, name, None)


def _user_keys(user: Any) -> list[str]:
    values: list[str] = []
    # usuario primero porque Fase 1 usa normalmente el login como usuario_key.
    for name in (
        "usuario", "username", "email", "id_usuario", "id",
        "nombre", "name", "user",
    ):
        value = str(_get(user, name) or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _global_role(user: Any) -> str:
    role = str(_get(user, "rol") or _get(user, "role") or "viewer").strip().lower()
    return role if role in {"owner", "admin", "operator", "viewer"} else "viewer"



# ---------------------------------------------------------------------------
# Administración de plataforma.
# Un owner/admin de una empresa NO es administrador de RackNova SaaS.
# ---------------------------------------------------------------------------

def is_platform_superadmin(session: Any, user: Any) -> bool:
    keys = _user_keys(user)
    if not keys:
        return False

    exists = session.connection().execute(
        sa_text("SELECT to_regclass('public.racknova_platform_admins')")
    ).scalar_one_or_none()
    if not exists:
        return False

    row = session.connection().execute(
        sa_text(
            """
            SELECT 1
            FROM racknova_platform_admins
            WHERE usuario_key = ANY(:keys)
              AND activo = TRUE
            LIMIT 1
            """
        ),
        {"keys": keys},
    ).first()
    return row is not None


def require_platform_superadmin(session: Any, user: Any) -> None:
    if not is_platform_superadmin(session, user):
        raise HTTPException(
            status_code=403,
            detail=(
                "Esta acción es exclusiva del Superadmin de RackNova. "
                "Ser owner/admin de una empresa no permite crear clientes nuevos."
            ),
        )


def platform_status(session: Any, user: Any) -> dict[str, Any]:
    return {
        "platform_superadmin": is_platform_superadmin(session, user),
        "empresas": memberships(session, user),
    }


def _ensure_legacy_membership(session: Any, user: Any) -> None:
    """Migra usuarios históricos SOLO mientras existe la empresa legacy única.

    Fase 1 auto-vinculaba usuarios sin membresía a RackNova Principal. Eso es
    útil durante la migración, pero sería peligroso después de vender a varios
    clientes: un usuario nuevo sin asignación podría entrar a Principal.

    En Fase 2 solo se permite ese respaldo si todavía no existen empresas
    comerciales adicionales.
    """
    keys = _user_keys(user)
    if not keys:
        raise HTTPException(status_code=401, detail="No se pudo identificar al usuario autenticado.")

    found = session.connection().execute(
        sa_text(
            """
            SELECT 1
            FROM empresa_usuarios
            WHERE usuario_key = ANY(:keys)
              AND activo = TRUE
            LIMIT 1
            """
        ),
        {"keys": keys},
    ).first()
    if found:
        return

    company_count = int(
        session.connection().execute(
            sa_text("SELECT COUNT(*) FROM empresas WHERE activo = TRUE")
        ).scalar_one()
        or 0
    )
    if company_count > 1:
        raise HTTPException(
            status_code=403,
            detail=(
                "Tu usuario todavía no está asignado a una empresa. "
                "Un administrador debe vincularlo antes de ingresar."
            ),
        )

    role = "owner" if _global_role(user) == "admin" else _global_role(user)
    session.connection().execute(
        sa_text(
            """
            INSERT INTO empresa_usuarios (
                id_empresa, usuario_key, nombre_usuario, rol, activo
            ) VALUES (
                CAST(:empresa AS UUID), :usuario, :nombre, :rol, TRUE
            )
            ON CONFLICT (id_empresa, usuario_key) DO NOTHING
            """
        ),
        {
            "empresa": DEFAULT_EMPRESA_ID,
            "usuario": keys[0],
            "nombre": str(_get(user, "nombre") or _get(user, "name") or keys[0]),
            "rol": role,
        },
    )
    session.commit()


def memberships(session: Any, user: Any) -> list[dict[str, Any]]:
    _ensure_legacy_membership(session, user)
    keys = _user_keys(user)
    rows = session.connection().execute(
        sa_text(
            """
            SELECT
                e.id_empresa,
                e.nombre,
                e.slug,
                e.activo,
                e.plan,
                e.moneda,
                e.zona_horaria,
                eu.rol,
                eu.usuario_key
            FROM empresa_usuarios eu
            JOIN empresas e ON e.id_empresa = eu.id_empresa
            WHERE eu.usuario_key = ANY(:keys)
              AND eu.activo = TRUE
              AND e.activo = TRUE
            ORDER BY
                CASE WHEN e.id_empresa = CAST(:principal AS UUID) THEN 0 ELSE 1 END,
                e.nombre
            """
        ),
        {"keys": keys, "principal": DEFAULT_EMPRESA_ID},
    ).mappings().all()
    return [dict(row) for row in rows]


def bind_empresa(
    session: Any,
    user: Any,
    requested_empresa_id: Optional[str] = None,
    allowed_roles: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Valida membresía + rol y liga el Session a una empresa."""
    rows = memberships(session, user)
    if not rows:
        raise HTTPException(status_code=403, detail="El usuario no pertenece a ninguna empresa activa.")

    requested = str(requested_empresa_id or "").strip()
    selected: Optional[dict[str, Any]] = None
    if requested:
        try:
            requested = str(UUID(requested))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="X-Empresa-ID no es un UUID válido.") from exc
        selected = next((row for row in rows if str(row["id_empresa"]) == requested), None)
        if selected is None:
            raise HTTPException(status_code=403, detail="No tienes acceso a la empresa solicitada.")
    else:
        selected = next(
            (row for row in rows if str(row["id_empresa"]) == DEFAULT_EMPRESA_ID),
            rows[0],
        )

    role = str(selected.get("rol") or "viewer").strip().lower()
    if allowed_roles and role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Tu rol en esta empresa no permite realizar esta acción.",
        )

    empresa_id = str(selected["id_empresa"])
    session.info[SESSION_EMPRESA_KEY] = empresa_id
    session.info[SESSION_ROL_KEY] = role
    # Rol transitorio del request: no es una columna mapeada y no se persiste.
    try:
        user.__dict__["_racknova_empresa_role"] = role
    except Exception:
        pass

    # Preparado también para RLS en Fase 4. No confiamos en esto como única barrera.
    session.connection().execute(
        sa_text("SELECT set_config('app.racknova_empresa_id', :empresa, true)"),
        {"empresa": empresa_id},
    )
    return selected


def current_empresa_id(session: Any) -> str:
    empresa = session.info.get(SESSION_EMPRESA_KEY)
    if not empresa:
        raise HTTPException(status_code=500, detail="La operación comercial no tiene empresa enlazada.")
    return str(empresa)


def current_empresa_role(session: Any) -> str:
    return str(session.info.get(SESSION_ROL_KEY) or "viewer")


# ---------------------------------------------------------------------------
# Usuarios globales + membresías por empresa.
# Usuario conserva identidad/login global; permisos y visibilidad son tenant.
# ---------------------------------------------------------------------------

def _membership_match_sql(alias: str = "u") -> str:
    return (
        f"(eu.usuario_key = {alias}.usuario "
        f"OR eu.usuario_key = CAST({alias}.id_usuario AS TEXT) "
        f"OR (eu.nombre_usuario IS NOT NULL AND eu.nombre_usuario = {alias}.nombre))"
    )


def company_users_payload(session: Any) -> list[dict[str, Any]]:
    empresa = current_empresa_id(session)
    rows = session.connection().execute(
        sa_text(
            f"""
            SELECT DISTINCT ON (u.id_usuario)
                u.id_usuario,
                u.usuario,
                u.nombre,
                eu.rol,
                eu.activo,
                u.fecha_creacion,
                u.ultima_actualizacion,
                u.ultimo_acceso
            FROM usuario u
            JOIN empresa_usuarios eu
              ON {_membership_match_sql('u')}
            WHERE eu.id_empresa = CAST(:empresa AS UUID)
            ORDER BY u.id_usuario, eu.actualizado_en DESC NULLS LAST, eu.creado_en DESC
            """
        ),
        {"empresa": empresa},
    ).mappings().all()
    return [dict(row) for row in rows]


def attach_user_membership(
    session: Any,
    *,
    usuario_key: str,
    nombre: Optional[str],
    rol: str,
    activo: bool = True,
) -> None:
    empresa = current_empresa_id(session)
    role = str(rol or "viewer").strip().lower()
    if role not in {"owner", "admin", "operator", "viewer"}:
        raise HTTPException(status_code=400, detail="Rol de empresa inválido.")
    session.connection().execute(
        sa_text(
            """
            INSERT INTO empresa_usuarios (
                id_empresa, usuario_key, nombre_usuario, rol, activo
            ) VALUES (
                CAST(:empresa AS UUID), :usuario, :nombre, :rol, :activo
            )
            ON CONFLICT (id_empresa, usuario_key)
            DO UPDATE SET
                nombre_usuario = EXCLUDED.nombre_usuario,
                rol = EXCLUDED.rol,
                activo = EXCLUDED.activo,
                actualizado_en = NOW()
            """
        ),
        {
            "empresa": empresa,
            "usuario": str(usuario_key).strip(),
            "nombre": nombre,
            "rol": role,
            "activo": bool(activo),
        },
    )


def company_user_guard(session: Any, id_usuario: int, *, single_only: bool = False) -> dict[str, Any]:
    empresa = current_empresa_id(session)
    row = session.connection().execute(
        sa_text(
            f"""
            SELECT
                eu.id_membresia,
                eu.usuario_key,
                eu.rol,
                eu.activo,
                u.usuario,
                u.nombre,
                (
                    SELECT COUNT(*)
                    FROM empresa_usuarios eu2
                    WHERE eu2.usuario_key IN (
                        u.usuario,
                        CAST(u.id_usuario AS TEXT),
                        COALESCE(u.nombre, '')
                    )
                ) AS total_membresias
            FROM usuario u
            JOIN empresa_usuarios eu
              ON {_membership_match_sql('u')}
            WHERE u.id_usuario = :id_usuario
              AND eu.id_empresa = CAST(:empresa AS UUID)
            ORDER BY eu.actualizado_en DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"empresa": empresa, "id_usuario": id_usuario},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa.")
    result = dict(row)
    if single_only and int(result.get("total_membresias") or 0) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Este usuario pertenece a varias empresas. Por seguridad, sus datos globales "
                "se administrarán desde el módulo multiempresa de la Fase 3."
            ),
        )
    return result


def update_membership_by_id(
    session: Any,
    id_membresia: Any,
    *,
    usuario_key: str,
    nombre: Optional[str],
    rol: str,
    activo: bool,
) -> None:
    role = str(rol or "viewer").strip().lower()
    if role == "owner":
        role = "admin"  # el endpoint legacy no transfiere ownership
    if role not in {"admin", "operator", "viewer"}:
        raise HTTPException(status_code=400, detail="Rol de empresa inválido.")
    session.connection().execute(
        sa_text(
            """
            UPDATE empresa_usuarios
            SET usuario_key = :usuario,
                nombre_usuario = :nombre,
                rol = :rol,
                activo = :activo,
                actualizado_en = NOW()
            WHERE id_membresia = CAST(:id AS UUID)
            """
        ),
        {
            "id": str(id_membresia),
            "usuario": str(usuario_key).strip(),
            "nombre": nombre,
            "rol": role,
            "activo": bool(activo),
        },
    )


def deactivate_membership_by_id(session: Any, id_membresia: Any) -> None:
    session.connection().execute(
        sa_text(
            """
            UPDATE empresa_usuarios
            SET activo = FALSE, actualizado_en = NOW()
            WHERE id_membresia = CAST(:id AS UUID)
            """
        ),
        {"id": str(id_membresia)},
    )


def count_current_admins(session: Any) -> int:
    empresa = current_empresa_id(session)
    value = session.connection().execute(
        sa_text(
            """
            SELECT COUNT(*)
            FROM empresa_usuarios
            WHERE id_empresa = CAST(:empresa AS UUID)
              AND activo = TRUE
              AND rol IN ('owner', 'admin')
            """
        ),
        {"empresa": empresa},
    ).scalar_one()
    return int(value or 0)


def raise_global_role_if_needed(session: Any, usuario_key: str, membership_role: str) -> None:
    """Mantiene el rol global como techo compatible con las dependencias legacy."""
    target = "admin" if membership_role in {"owner", "admin"} else membership_role
    rank = {"viewer": 1, "operator": 2, "admin": 3}
    row = session.connection().execute(
        sa_text(
            """
            SELECT id_usuario, rol
            FROM usuario
            WHERE usuario = :key OR CAST(id_usuario AS TEXT) = :key OR nombre = :key
            LIMIT 1
            """
        ),
        {"key": str(usuario_key)},
    ).mappings().first()
    if not row:
        return
    current = str(row.get("rol") or "viewer").lower()
    if rank.get(target, 1) > rank.get(current, 1):
        session.connection().execute(
            sa_text("UPDATE usuario SET rol = :rol WHERE id_usuario = :id"),
            {"rol": target, "id": row["id_usuario"]},
        )
        session.commit()


# ---------------------------------------------------------------------------
# Aislamiento ORM automático.
# ---------------------------------------------------------------------------

def _tenant_entities(statement: Any) -> Iterable[Any]:
    try:
        descriptions = statement.column_descriptions
    except Exception:
        descriptions = []
    seen: set[Any] = set()
    for item in descriptions or []:
        entity = item.get("entity") if isinstance(item, dict) else None
        if entity is not None and entity not in seen and hasattr(entity, "empresa_id"):
            seen.add(entity)
            yield entity


def install_events() -> None:
    global _EVENTS_INSTALLED
    if _EVENTS_INSTALLED:
        return
    _EVENTS_INSTALLED = True

    @event.listens_for(SASession, "after_begin")
    def _after_begin(session: SASession, transaction: Any, connection: Any) -> None:
        empresa = session.info.get(SESSION_EMPRESA_KEY)
        if empresa:
            connection.execute(
                sa_text("SELECT set_config('app.racknova_empresa_id', :empresa, true)"),
                {"empresa": str(empresa)},
            )

    @event.listens_for(SASession, "before_flush")
    def _before_flush(session: SASession, flush_context: Any, instances: Any) -> None:
        empresa = session.info.get(SESSION_EMPRESA_KEY)
        tenant_objects = [
            obj for obj in list(session.new) + list(session.dirty) + list(session.deleted)
            if hasattr(obj, "empresa_id")
        ]
        if tenant_objects and not empresa:
            raise HTTPException(
                status_code=500,
                detail="Operación comercial bloqueada: no se enlazó una empresa al request.",
            )
        if not empresa:
            return

        tenant_uuid = UUID(str(empresa))
        for obj in list(session.new):
            if hasattr(obj, "empresa_id"):
                setattr(obj, "empresa_id", tenant_uuid)
        for obj in list(session.dirty) + list(session.deleted):
            if not hasattr(obj, "empresa_id"):
                continue
            actual = getattr(obj, "empresa_id", None)
            if actual is None or str(actual) != str(tenant_uuid):
                raise HTTPException(
                    status_code=403,
                    detail="La operación intenta modificar información de otra empresa.",
                )

    @event.listens_for(SASession, "do_orm_execute")
    def _do_orm_execute(execute_state: Any) -> None:
        empresa = execute_state.session.info.get(SESSION_EMPRESA_KEY)
        if not empresa:
            return
        tenant_uuid = UUID(str(empresa))
        statement = execute_state.statement

        if execute_state.is_select:
            for entity in _tenant_entities(statement):
                statement = statement.where(entity.empresa_id == tenant_uuid)
            execute_state.statement = statement
            return

        if execute_state.is_update or execute_state.is_delete:
            table = getattr(statement, "table", None)
            columns = getattr(table, "c", None)
            if columns is not None and "empresa_id" in columns:
                execute_state.statement = statement.where(columns.empresa_id == tenant_uuid)


install_events()
