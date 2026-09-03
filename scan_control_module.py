from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Field, Session, SQLModel, select

from multiempresa_tenant import bind_empresa as _rn_bind_empresa


# ==========================================================
# RACKNOVA SCAN CONTROL + LOCATION IDENTITY
# Fase 3: capacidades opcionales, controladas por la empresa.
# ==========================================================


class RackNovaScanConfiguracion(SQLModel, table=True):
    __tablename__ = "racknova_scan_configuracion"

    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)
    id_configuracion: UUID = Field(default_factory=uuid4, primary_key=True)

    pos_verificacion_requerida: bool = False
    ubicacion_verificacion_requerida: bool = False
    hid_habilitado: bool = True
    camara_habilitada: bool = True

    fecha_actualizacion: datetime
    actualizado_por: str = "Sistema"


class RackNovaUbicacionIdentidad(SQLModel, table=True):
    __tablename__ = "racknova_ubicacion_identidad"

    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)
    id_ubicacion: UUID = Field(default_factory=uuid4, primary_key=True)
    codigo_ubicacion: str = Field(index=True, unique=True)

    nombre: str = Field(index=True)
    rack: Optional[str] = Field(default=None, index=True)
    nivel: Optional[str] = Field(default=None, index=True)
    posicion: Optional[str] = Field(default=None, index=True)
    descripcion: Optional[str] = None
    activa: bool = Field(default=True, index=True)

    fecha_creacion: datetime
    fecha_actualizacion: datetime
    creado_por: str = "Sistema"
    actualizado_por: str = "Sistema"


class ScanConfiguracionUpdate(BaseModel):
    pos_verificacion_requerida: Optional[bool] = None
    ubicacion_verificacion_requerida: Optional[bool] = None
    hid_habilitado: Optional[bool] = None
    camara_habilitada: Optional[bool] = None


class UbicacionCreate(BaseModel):
    nombre: str = PydanticField(min_length=2, max_length=120)
    rack: Optional[str] = PydanticField(default=None, max_length=80)
    nivel: Optional[str] = PydanticField(default=None, max_length=80)
    posicion: Optional[str] = PydanticField(default=None, max_length=80)
    descripcion: Optional[str] = PydanticField(default=None, max_length=300)


class UbicacionUpdate(BaseModel):
    nombre: Optional[str] = PydanticField(default=None, min_length=2, max_length=120)
    rack: Optional[str] = PydanticField(default=None, max_length=80)
    nivel: Optional[str] = PydanticField(default=None, max_length=80)
    posicion: Optional[str] = PydanticField(default=None, max_length=80)
    descripcion: Optional[str] = PydanticField(default=None, max_length=300)
    activa: Optional[bool] = None


def _usuario_nombre(current_user: Any) -> str:
    if isinstance(current_user, dict):
        return str(
            current_user.get("nombre")
            or current_user.get("usuario")
            or current_user.get("username")
            or "Usuario"
        )
    return str(
        getattr(current_user, "nombre", None)
        or getattr(current_user, "usuario", None)
        or getattr(current_user, "username", None)
        or "Usuario"
    )


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _config_payload(row: Optional[RackNovaScanConfiguracion]) -> dict[str, Any]:
    if row is None:
        return {
            "pos_verificacion_requerida": False,
            "ubicacion_verificacion_requerida": False,
            "hid_habilitado": True,
            "camara_habilitada": True,
            "fecha_actualizacion": None,
            "actualizado_por": None,
        }
    return {
        "pos_verificacion_requerida": bool(row.pos_verificacion_requerida),
        "ubicacion_verificacion_requerida": bool(row.ubicacion_verificacion_requerida),
        "hid_habilitado": bool(row.hid_habilitado),
        "camara_habilitada": bool(row.camara_habilitada),
        "fecha_actualizacion": row.fecha_actualizacion,
        "actualizado_por": row.actualizado_por,
    }


def _ubicacion_payload(row: RackNovaUbicacionIdentidad) -> dict[str, Any]:
    return {
        "id_ubicacion": str(row.id_ubicacion),
        "codigo_ubicacion": row.codigo_ubicacion,
        "nombre": row.nombre,
        "rack": row.rack,
        "nivel": row.nivel,
        "posicion": row.posicion,
        "descripcion": row.descripcion,
        "activa": bool(row.activa),
        "fecha_creacion": row.fecha_creacion,
        "fecha_actualizacion": row.fecha_actualizacion,
        "creado_por": row.creado_por,
        "actualizado_por": row.actualizado_por,
    }


def registrar_modulo_scan_control(
    *,
    app: Any,
    get_session: Callable[..., Any],
    require_roles: Callable[..., Any],
    mexico_now: Callable[[], datetime],
) -> None:
    read_user = require_roles("owner", "admin", "operator", "viewer")
    operator_user = require_roles("owner", "admin", "operator")
    admin_user = require_roles("owner", "admin")

    def bind_read(session: Session, current_user: Any, empresa: Optional[str]) -> None:
        _rn_bind_empresa(
            session,
            current_user,
            empresa,
            allowed_roles={"owner", "admin", "operator", "viewer"},
        )

    def bind_operator(session: Session, current_user: Any, empresa: Optional[str]) -> None:
        _rn_bind_empresa(
            session,
            current_user,
            empresa,
            allowed_roles={"owner", "admin", "operator"},
        )

    def bind_admin(session: Session, current_user: Any, empresa: Optional[str]) -> None:
        _rn_bind_empresa(
            session,
            current_user,
            empresa,
            allowed_roles={"owner", "admin"},
        )

    @app.get("/scan/configuracion")
    def obtener_scan_configuracion(
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_read(session, current_user, rn_empresa_id)
        row = session.exec(select(RackNovaScanConfiguracion)).first()
        return _config_payload(row)

    @app.put("/scan/configuracion")
    def actualizar_scan_configuracion(
        data: ScanConfiguracionUpdate,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_admin(session, current_user, rn_empresa_id)
        row = session.exec(select(RackNovaScanConfiguracion)).first()
        now = mexico_now()
        actor = _usuario_nombre(current_user)

        if row is None:
            row = RackNovaScanConfiguracion(
                fecha_actualizacion=now,
                actualizado_por=actor,
            )

        values = data.model_dump(exclude_none=True)
        for key, value in values.items():
            setattr(row, key, bool(value))

        row.fecha_actualizacion = now
        row.actualizado_por = actor
        session.add(row)
        session.commit()
        session.refresh(row)
        return _config_payload(row)

    @app.get("/scan/ubicaciones")
    def listar_ubicaciones_scan(
        incluir_inactivas: bool = False,
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_read(session, current_user, rn_empresa_id)
        stmt = select(RackNovaUbicacionIdentidad)
        if not incluir_inactivas:
            stmt = stmt.where(RackNovaUbicacionIdentidad.activa == True)  # noqa: E712
        stmt = stmt.order_by(RackNovaUbicacionIdentidad.nombre)
        return [_ubicacion_payload(row) for row in session.exec(stmt).all()]

    @app.get("/scan/ubicaciones/codigo/{codigo_ubicacion:path}")
    def obtener_ubicacion_por_codigo(
        codigo_ubicacion: str,
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_read(session, current_user, rn_empresa_id)
        code = str(codigo_ubicacion).strip()
        row = session.exec(
            select(RackNovaUbicacionIdentidad).where(
                RackNovaUbicacionIdentidad.codigo_ubicacion == code
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Ubicación no registrada.")
        return _ubicacion_payload(row)

    @app.post("/scan/ubicaciones")
    def crear_ubicacion_scan(
        data: UbicacionCreate,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_operator(session, current_user, rn_empresa_id)
        now = mexico_now()
        actor = _usuario_nombre(current_user)
        location_id = uuid4()
        row = RackNovaUbicacionIdentidad(
            id_ubicacion=location_id,
            codigo_ubicacion=f"RNLOC:{location_id}",
            nombre=data.nombre.strip(),
            rack=_clean(data.rack),
            nivel=_clean(data.nivel),
            posicion=_clean(data.posicion),
            descripcion=_clean(data.descripcion),
            activa=True,
            fecha_creacion=now,
            fecha_actualizacion=now,
            creado_por=actor,
            actualizado_por=actor,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _ubicacion_payload(row)

    @app.put("/scan/ubicaciones/{id_ubicacion}")
    def actualizar_ubicacion_scan(
        id_ubicacion: UUID,
        data: UbicacionUpdate,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_operator(session, current_user, rn_empresa_id)
        row = session.get(RackNovaUbicacionIdentidad, id_ubicacion)
        if row is None:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada.")

        values = data.model_dump(exclude_unset=True)
        if "nombre" in values and values["nombre"] is not None:
            row.nombre = str(values["nombre"]).strip()
        for field_name in ("rack", "nivel", "posicion", "descripcion"):
            if field_name in values:
                setattr(row, field_name, _clean(values[field_name]))
        if "activa" in values and values["activa"] is not None:
            row.activa = bool(values["activa"])

        row.fecha_actualizacion = mexico_now()
        row.actualizado_por = _usuario_nombre(current_user)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _ubicacion_payload(row)

    @app.delete("/scan/ubicaciones/{id_ubicacion}")
    def desactivar_ubicacion_scan(
        id_ubicacion: UUID,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind_admin(session, current_user, rn_empresa_id)
        row = session.get(RackNovaUbicacionIdentidad, id_ubicacion)
        if row is None:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada.")
        row.activa = False
        row.fecha_actualizacion = mexico_now()
        row.actualizado_por = _usuario_nombre(current_user)
        session.add(row)
        session.commit()
        return {"ok": True, "id_ubicacion": str(row.id_ubicacion)}
