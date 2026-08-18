from __future__ import annotations
from uuid import UUID
from fastapi import Header
from multiempresa_tenant import bind_empresa as _rn_bind_empresa

import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import or_
from sqlmodel import Field, Session, SQLModel, select


# ==========================================================
# MODELOS POS — FASE 1 + CAJA PROFESIONAL
# ==========================================================


class POSConfiguracion(SQLModel, table=True):
    __tablename__ = "pos_configuracion"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_configuracion: Optional[int] = Field(default=None, primary_key=True)
    activo: bool = True
    fecha_actualizacion: datetime
    actualizado_por: str = "Sistema"


class VentaPOS(SQLModel, table=True):
    __tablename__ = "venta_pos"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_venta: Optional[int] = Field(default=None, primary_key=True)
    folio: str = Field(index=True)
    usuario: str
    subtotal: float = 0
    descuento_total: float = 0
    total: float = 0
    costo_total: float = 0
    ganancia: float = 0
    efectivo_recibido: float = 0
    cambio: float = 0
    estado: str = "COMPLETADA"
    fecha: datetime


class VentaPOSDetalle(SQLModel, table=True):
    __tablename__ = "venta_pos_detalle"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_detalle: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    id_producto: Optional[int] = Field(default=None, index=True)
    sku: str = Field(index=True)
    codigo_barras: Optional[str] = Field(default=None, index=True)
    nombre: str
    cantidad: int
    precio_lista: float
    descuento_porcentaje: float = 0
    precio_unitario_final: float
    subtotal: float
    costo_unitario: float
    costo_total: float
    ganancia: float


class VentaPOSPago(SQLModel, table=True):
    __tablename__ = "venta_pos_pago"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_pago: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    metodo: str
    monto: float
    referencia: Optional[str] = None


class POSCaja(SQLModel, table=True):
    __tablename__ = "pos_caja"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_caja: Optional[int] = Field(default=None, primary_key=True)
    nombre: str = Field(index=True)
    activa: bool = True
    fecha_creacion: datetime
    creada_por: str = "Sistema"


class POSSesionCaja(SQLModel, table=True):
    __tablename__ = "pos_sesion_caja"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_sesion: Optional[int] = Field(default=None, primary_key=True)
    id_caja: int = Field(index=True)
    caja_nombre: str
    usuario: str = Field(index=True)
    fondo_inicial: float = 0
    fecha_apertura: datetime
    fecha_cierre: Optional[datetime] = None
    efectivo_esperado: float = 0
    efectivo_contado: Optional[float] = None
    diferencia: Optional[float] = None
    estado: str = Field(default="ABIERTA", index=True)
    observaciones: Optional[str] = None


class POSMovimientoEfectivo(SQLModel, table=True):
    __tablename__ = "pos_movimiento_efectivo"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_movimiento: Optional[int] = Field(default=None, primary_key=True)
    id_sesion: int = Field(index=True)
    tipo: str = Field(index=True)
    monto: float
    motivo: str
    usuario: str
    fecha: datetime


class POSVentaControl(SQLModel, table=True):
    __tablename__ = "pos_venta_control"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_control: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    id_sesion: int = Field(index=True)
    operacion_id: str = Field(index=True)
    fecha_creacion: datetime
    anulada_por: Optional[str] = None
    motivo_anulacion: Optional[str] = None
    fecha_anulacion: Optional[datetime] = None


class POSVentaLote(SQLModel, table=True):
    __tablename__ = "pos_venta_lote"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_registro: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    id_detalle: int = Field(index=True)
    id_lote: Optional[int] = Field(default=None, index=True)
    sku: str = Field(index=True)
    cantidad: int
    cantidad_restaurada: int = 0


class POSVentaMovimiento(SQLModel, table=True):
    __tablename__ = "pos_venta_movimiento"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_registro: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    id_detalle: int = Field(index=True)
    id_movimiento: int = Field(index=True)


class POSDevolucion(SQLModel, table=True):
    __tablename__ = "pos_devolucion"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_devolucion: Optional[int] = Field(default=None, primary_key=True)
    folio: str = Field(index=True)
    id_venta: int = Field(index=True)
    id_sesion: int = Field(index=True)
    usuario: str
    motivo: str
    metodo_reembolso: str
    monto: float = 0
    estado: str = "COMPLETADA"
    fecha: datetime


class POSDevolucionDetalle(SQLModel, table=True):
    __tablename__ = "pos_devolucion_detalle"
    # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST_MODEL
    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)

    id_detalle_devolucion: Optional[int] = Field(default=None, primary_key=True)
    id_devolucion: int = Field(index=True)
    id_detalle_venta: int = Field(index=True)
    id_producto: Optional[int] = Field(default=None, index=True)
    sku: str = Field(index=True)
    nombre: str
    cantidad: int
    precio_unitario: float
    subtotal: float


# ==========================================================
# REQUESTS
# ==========================================================


class POSConfiguracionRequest(BaseModel):
    activo: bool


class POSVentaItemRequest(BaseModel):
    sku: str = PydanticField(min_length=1)
    cantidad: int = PydanticField(gt=0)
    descuento_porcentaje: float = PydanticField(default=0, ge=0, le=100)


class POSPagoRequest(BaseModel):
    metodo: str = PydanticField(min_length=1)
    monto: float = PydanticField(gt=0)
    referencia: Optional[str] = None


class POSVentaRequest(BaseModel):
    operacion_id: str = PydanticField(min_length=8, max_length=100)
    items: List[POSVentaItemRequest] = PydanticField(min_length=1)
    pagos: List[POSPagoRequest] = PydanticField(min_length=1)
    efectivo_recibido: Optional[float] = PydanticField(default=None, ge=0)


class POSCajaRequest(BaseModel):
    nombre: str = PydanticField(min_length=2, max_length=80)


class POSCajaEstadoRequest(BaseModel):
    activa: bool


class POSAbrirCajaRequest(BaseModel):
    id_caja: int = PydanticField(gt=0)
    fondo_inicial: float = PydanticField(default=0, ge=0)


class POSCerrarCajaRequest(BaseModel):
    efectivo_contado: float = PydanticField(ge=0)
    observaciones: Optional[str] = PydanticField(default=None, max_length=500)


class POSMovimientoEfectivoRequest(BaseModel):
    tipo: str = PydanticField(min_length=1)
    monto: float = PydanticField(gt=0)
    motivo: str = PydanticField(min_length=3, max_length=300)


class POSCancelarVentaRequest(BaseModel):
    motivo: str = PydanticField(min_length=3, max_length=500)


class POSDevolucionItemRequest(BaseModel):
    id_detalle: int = PydanticField(gt=0)
    cantidad: int = PydanticField(gt=0)


class POSDevolucionRequest(BaseModel):
    items: List[POSDevolucionItemRequest] = PydanticField(min_length=1)
    motivo: str = PydanticField(min_length=3, max_length=500)
    metodo_reembolso: str = PydanticField(min_length=1)


# ==========================================================
# UTILIDADES
# ==========================================================


def _env_pos_habilitado() -> bool:
    value = os.getenv("POS_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "si", "sí", "on"}


def _dinero(value: Any) -> float:
    return round(float(value or 0), 2)


def _usuario_nombre(current_user: Any) -> str:
    return str(
        getattr(current_user, "nombre", None)
        or getattr(current_user, "usuario", None)
        or "Usuario"
    )


def _usuario_clave(current_user: Any) -> str:
    return str(
        getattr(current_user, "usuario", None)
        or getattr(current_user, "nombre", None)
        or "Usuario"
    )


def _usuario_rol(current_user: Any) -> str:
    return str(getattr(current_user, "_racknova_empresa_role", None) or getattr(current_user, "rol", "operator") or "operator").lower()


def _obtener_configuracion(session: Session) -> Optional[POSConfiguracion]:
    return session.exec(select(POSConfiguracion)).first()


def _config_pos_habilitado(session: Session) -> bool:
    config = _obtener_configuracion(session)
    return True if config is None else bool(config.activo)


def _pos_habilitado(session: Session) -> bool:
    return _env_pos_habilitado() and _config_pos_habilitado(session)


def _exigir_pos_habilitado(session: Session) -> None:
    if not _pos_habilitado(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El módulo Punto de Venta está desactivado.",
        )


def _folio_venta(fecha: datetime) -> str:
    return f"RN-{fecha:%Y%m%d-%H%M%S}-{uuid4().hex[:6].upper()}"


def _folio_devolucion(fecha: datetime) -> str:
    return f"DEV-{fecha:%Y%m%d-%H%M%S}-{uuid4().hex[:5].upper()}"


def _sesion_abierta_usuario(
    session: Session,
    current_user: Any,
    bloquear: bool = False,
) -> Optional[POSSesionCaja]:
    statement = select(POSSesionCaja).where(
        (POSSesionCaja.usuario == _usuario_clave(current_user))
        & (POSSesionCaja.estado == "ABIERTA")
    )
    if bloquear:
        try:
            statement = statement.with_for_update()
        except AttributeError:
            pass
    return session.exec(statement).first()


def _exigir_sesion_abierta(
    session: Session,
    current_user: Any,
    bloquear: bool = False,
) -> POSSesionCaja:
    sesion = _sesion_abierta_usuario(session, current_user, bloquear=bloquear)
    if sesion is None:
        raise HTTPException(
            status_code=409,
            detail="Debes abrir una caja antes de registrar ventas.",
        )
    return sesion


def _venta_control(session: Session, id_venta: int) -> Optional[POSVentaControl]:
    return session.exec(
        select(POSVentaControl).where(POSVentaControl.id_venta == id_venta)
    ).first()


def _total_devuelto_detalle(session: Session, id_detalle: int) -> int:
    registros = session.exec(
        select(POSDevolucionDetalle).where(
            POSDevolucionDetalle.id_detalle_venta == id_detalle
        )
    ).all()
    return sum(int(item.cantidad or 0) for item in registros)


def _calcular_sesion(
    session: Session,
    sesion: POSSesionCaja,
) -> Dict[str, Any]:
    controles = session.exec(
        select(POSVentaControl).where(POSVentaControl.id_sesion == sesion.id_sesion)
    ).all()
    ids_ventas = [item.id_venta for item in controles]

    ventas: List[VentaPOS] = []
    pagos: List[VentaPOSPago] = []
    if ids_ventas:
        ventas = session.exec(
            select(VentaPOS).where(VentaPOS.id_venta.in_(ids_ventas))
        ).all()
        pagos = session.exec(
            select(VentaPOSPago).where(VentaPOSPago.id_venta.in_(ids_ventas))
        ).all()

    ventas_validas = [venta for venta in ventas if venta.estado == "COMPLETADA"]
    ids_validas = {venta.id_venta for venta in ventas_validas}
    pagos_validos = [pago for pago in pagos if pago.id_venta in ids_validas]

    efectivo_ventas = _dinero(
        sum(pago.monto for pago in pagos_validos if pago.metodo == "efectivo")
    )
    tarjeta = _dinero(
        sum(pago.monto for pago in pagos_validos if pago.metodo == "tarjeta")
    )
    transferencia = _dinero(
        sum(pago.monto for pago in pagos_validos if pago.metodo == "transferencia")
    )
    total_ventas = _dinero(sum(venta.total for venta in ventas_validas))

    movimientos = session.exec(
        select(POSMovimientoEfectivo).where(
            POSMovimientoEfectivo.id_sesion == sesion.id_sesion
        )
    ).all()
    entradas = _dinero(
        sum(item.monto for item in movimientos if item.tipo in {"ENTRADA", "AJUSTE_ENTRADA"})
    )
    salidas = _dinero(
        sum(
            item.monto
            for item in movimientos
            if item.tipo in {"RETIRO", "GASTO", "DEPOSITO", "AJUSTE_SALIDA"}
        )
    )

    devoluciones = session.exec(
        select(POSDevolucion).where(
            (POSDevolucion.id_sesion == sesion.id_sesion)
            & (POSDevolucion.estado == "COMPLETADA")
        )
    ).all()
    reembolsos_efectivo = _dinero(
        sum(
            item.monto
            for item in devoluciones
            if item.metodo_reembolso == "efectivo"
        )
    )

    esperado = _dinero(
        sesion.fondo_inicial
        + efectivo_ventas
        + entradas
        - salidas
        - reembolsos_efectivo
    )

    return {
        "id_sesion": sesion.id_sesion,
        "id_caja": sesion.id_caja,
        "caja_nombre": sesion.caja_nombre,
        "usuario": sesion.usuario,
        "estado": sesion.estado,
        "fondo_inicial": _dinero(sesion.fondo_inicial),
        "fecha_apertura": sesion.fecha_apertura,
        "fecha_cierre": sesion.fecha_cierre,
        "ventas_completadas": len(ventas_validas),
        "ventas_canceladas": len([v for v in ventas if v.estado == "CANCELADA"]),
        "total_ventas": total_ventas,
        "efectivo_ventas": efectivo_ventas,
        "tarjeta": tarjeta,
        "transferencia": transferencia,
        "entradas_efectivo": entradas,
        "salidas_efectivo": salidas,
        "reembolsos_efectivo": reembolsos_efectivo,
        "efectivo_esperado": esperado,
        "efectivo_contado": (
            None if sesion.efectivo_contado is None else _dinero(sesion.efectivo_contado)
        ),
        "diferencia": None if sesion.diferencia is None else _dinero(sesion.diferencia),
        "observaciones": sesion.observaciones,
        "movimientos_efectivo": [
            {
                "id_movimiento": item.id_movimiento,
                "tipo": item.tipo,
                "monto": _dinero(item.monto),
                "motivo": item.motivo,
                "usuario": item.usuario,
                "fecha": item.fecha,
            }
            for item in sorted(movimientos, key=lambda row: row.fecha, reverse=True)
        ],
    }


def _serializar_venta(
    session: Session,
    venta: VentaPOS,
    incluir_detalle: bool = True,
) -> Dict[str, Any]:
    control = _venta_control(session, int(venta.id_venta or 0))
    data: Dict[str, Any] = {
        "id_venta": venta.id_venta,
        "folio": venta.folio,
        "usuario": venta.usuario,
        "subtotal": _dinero(venta.subtotal),
        "descuento_total": _dinero(venta.descuento_total),
        "total": _dinero(venta.total),
        "costo_total": _dinero(venta.costo_total),
        "ganancia": _dinero(venta.ganancia),
        "efectivo_recibido": _dinero(venta.efectivo_recibido),
        "cambio": _dinero(venta.cambio),
        "estado": venta.estado,
        "fecha": venta.fecha,
        "id_sesion": control.id_sesion if control else None,
        "operacion_id": control.operacion_id if control else None,
        "motivo_anulacion": control.motivo_anulacion if control else None,
        "fecha_anulacion": control.fecha_anulacion if control else None,
    }

    if incluir_detalle and venta.id_venta is not None:
        detalles = session.exec(
            select(VentaPOSDetalle).where(VentaPOSDetalle.id_venta == venta.id_venta)
        ).all()
        pagos = session.exec(
            select(VentaPOSPago).where(VentaPOSPago.id_venta == venta.id_venta)
        ).all()
        data["items"] = [
            {
                "id_detalle": item.id_detalle,
                "id_producto": item.id_producto,
                "sku": item.sku,
                "codigo_barras": item.codigo_barras,
                "nombre": item.nombre,
                "cantidad": item.cantidad,
                "cantidad_devuelta": _total_devuelto_detalle(
                    session, int(item.id_detalle or 0)
                ),
                "precio_lista": _dinero(item.precio_lista),
                "descuento_porcentaje": _dinero(item.descuento_porcentaje),
                "precio_unitario_final": _dinero(item.precio_unitario_final),
                "subtotal": _dinero(item.subtotal),
                "costo_unitario": _dinero(item.costo_unitario),
                "costo_total": _dinero(item.costo_total),
                "ganancia": _dinero(item.ganancia),
            }
            for item in detalles
        ]
        data["pagos"] = [
            {
                "id_pago": pago.id_pago,
                "metodo": pago.metodo,
                "monto": _dinero(pago.monto),
                "referencia": pago.referencia,
            }
            for pago in pagos
        ]
    return data


def _restaurar_lotes(
    *,
    session: Session,
    detalle: VentaPOSDetalle,
    cantidad: int,
    ProductoLote: Any,
    mexico_now: Callable[[], datetime],
) -> None:
    restante = cantidad
    asignaciones = session.exec(
        select(POSVentaLote)
        .where(POSVentaLote.id_detalle == detalle.id_detalle)
        .order_by(POSVentaLote.id_registro)
    ).all()

    for asignacion in asignaciones:
        if restante <= 0:
            break
        disponible = max(
            int(asignacion.cantidad or 0) - int(asignacion.cantidad_restaurada or 0),
            0,
        )
        restaurar = min(disponible, restante)
        if restaurar <= 0:
            continue
        lote = session.get(ProductoLote, asignacion.id_lote) if asignacion.id_lote else None
        if lote is not None:
            lote.cantidad_actual = int(lote.cantidad_actual or 0) + restaurar
            session.add(lote)
            asignacion.cantidad_restaurada = int(
                asignacion.cantidad_restaurada or 0
            ) + restaurar
            session.add(asignacion)
            restante -= restaurar

    if restante > 0:
        # Compatibilidad con ventas anteriores o lotes eliminados.
        session.add(
            ProductoLote(
                sku=detalle.sku,
                nombre=detalle.nombre,
                cantidad_inicial=restante,
                cantidad_actual=restante,
                costo_unitario=_dinero(detalle.costo_unitario),
                caducidad=None,
                fecha_ingreso=mexico_now(),
            )
        )


def _actualizar_movimiento_neto(
    *,
    session: Session,
    detalle: VentaPOSDetalle,
    Movimiento: Any,
) -> None:
    devuelto = _total_devuelto_detalle(session, int(detalle.id_detalle or 0))
    restante = max(int(detalle.cantidad or 0) - devuelto, 0)
    vinculo = session.exec(
        select(POSVentaMovimiento).where(
            POSVentaMovimiento.id_detalle == detalle.id_detalle
        )
    ).first()
    if not vinculo:
        return
    movimiento = session.get(Movimiento, vinculo.id_movimiento)
    if not movimiento:
        return
    ingreso = _dinero(detalle.precio_unitario_final * restante)
    costo = _dinero(detalle.costo_unitario * restante)
    movimiento.cantidad = restante
    movimiento.precio_venta = _dinero(detalle.precio_unitario_final)
    movimiento.ingreso_total = ingreso
    movimiento.costo_total = costo
    movimiento.ganancia = _dinero(ingreso - costo)
    session.add(movimiento)


# ==========================================================
# REGISTRO DE RUTAS
# ==========================================================


def registrar_modulo_pos(
    *,
    app: Any,
    get_session: Callable[..., Any],
    require_roles: Callable[..., Any],
    Producto: Any,
    ProductoLote: Any,
    Movimiento: Any,
    mexico_now: Callable[[], datetime],
    descontar_lotes_fefo: Callable[..., List[Dict[str, Any]]],
    obtener_caducidad_mas_proxima: Callable[..., Any],
) -> None:
    read_user = require_roles("admin", "operator", "viewer")
    operator_user = require_roles("admin", "operator")
    admin_user = require_roles("admin")

    @app.get("/pos/estado")
    def obtener_estado_pos(
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner', 'viewer'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        config = _obtener_configuracion(session)
        return {
            "habilitado": _pos_habilitado(session),
            "env_habilitado": _env_pos_habilitado(),
            "config_habilitado": True if config is None else bool(config.activo),
            "mensaje": (
                "Punto de Venta disponible."
                if _pos_habilitado(session)
                else "Punto de Venta desactivado."
            ),
        }

    @app.put("/pos/configuracion")
    def actualizar_configuracion_pos(
        data: POSConfiguracionRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        config = _obtener_configuracion(session)
        ahora = mexico_now()
        if config is None:
            config = POSConfiguracion(
                activo=data.activo,
                fecha_actualizacion=ahora,
                actualizado_por=_usuario_nombre(current_user),
            )
        else:
            config.activo = data.activo
            config.fecha_actualizacion = ahora
            config.actualizado_por = _usuario_nombre(current_user)
        session.add(config)
        session.commit()
        return {
            "habilitado": _pos_habilitado(session),
            "env_habilitado": _env_pos_habilitado(),
            "config_habilitado": bool(config.activo),
            "mensaje": (
                "Punto de Venta activado."
                if config.activo
                else "Punto de Venta desactivado."
            ),
        }

    # ------------------------------------------------------
    # CAJAS Y SESIONES
    # ------------------------------------------------------

    @app.get("/pos/cajas")
    def listar_cajas_pos(
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        cajas = session.exec(select(POSCaja).order_by(POSCaja.nombre)).all()
        return [
            {
                "id_caja": caja.id_caja,
                "nombre": caja.nombre,
                "activa": caja.activa,
                "fecha_creacion": caja.fecha_creacion,
                "creada_por": caja.creada_por,
            }
            for caja in cajas
        ]

    @app.post("/pos/cajas")
    def crear_caja_pos(
        data: POSCajaRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        nombre = data.nombre.strip()
        existente = session.exec(select(POSCaja).where(POSCaja.nombre == nombre)).first()
        if existente:
            raise HTTPException(status_code=409, detail="Ya existe una caja con ese nombre.")
        caja = POSCaja(
            nombre=nombre,
            activa=True,
            fecha_creacion=mexico_now(),
            creada_por=_usuario_nombre(current_user),
        )
        session.add(caja)
        session.commit()
        session.refresh(caja)
        return {
            "id_caja": caja.id_caja,
            "nombre": caja.nombre,
            "activa": caja.activa,
            "mensaje": "Caja creada correctamente.",
        }

    @app.put("/pos/cajas/{id_caja}/estado")
    def cambiar_estado_caja_pos(
        id_caja: int,
        data: POSCajaEstadoRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        caja = session.get(POSCaja, id_caja)
        if not caja:
            raise HTTPException(status_code=404, detail="Caja no encontrada.")
        if not data.activa:
            abierta = session.exec(
                select(POSSesionCaja).where(
                    (POSSesionCaja.id_caja == id_caja)
                    & (POSSesionCaja.estado == "ABIERTA")
                )
            ).first()
            if abierta:
                raise HTTPException(
                    status_code=409,
                    detail="No puedes desactivar una caja con una sesión abierta.",
                )
        caja.activa = data.activa
        session.add(caja)
        session.commit()
        return {"id_caja": caja.id_caja, "activa": caja.activa}

    @app.get("/pos/caja/sesion-actual")
    def obtener_sesion_actual_pos(
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        sesion = _sesion_abierta_usuario(session, current_user)
        if not sesion:
            return {"abierta": False, "sesion": None}
        return {"abierta": True, "sesion": _calcular_sesion(session, sesion)}

    @app.post("/pos/caja/abrir")
    def abrir_caja_pos(
        data: POSAbrirCajaRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        if _sesion_abierta_usuario(session, current_user):
            raise HTTPException(status_code=409, detail="Ya tienes una caja abierta.")
        caja = session.get(POSCaja, data.id_caja)
        if not caja or not caja.activa:
            raise HTTPException(status_code=404, detail="Caja activa no encontrada.")
        ocupada = session.exec(
            select(POSSesionCaja).where(
                (POSSesionCaja.id_caja == caja.id_caja)
                & (POSSesionCaja.estado == "ABIERTA")
            )
        ).first()
        if ocupada:
            raise HTTPException(status_code=409, detail="Esa caja ya está siendo utilizada.")
        sesion_caja = POSSesionCaja(
            id_caja=int(caja.id_caja or 0),
            caja_nombre=caja.nombre,
            usuario=_usuario_clave(current_user),
            fondo_inicial=_dinero(data.fondo_inicial),
            fecha_apertura=mexico_now(),
            estado="ABIERTA",
        )
        session.add(sesion_caja)
        session.commit()
        session.refresh(sesion_caja)
        return {
            "mensaje": "Caja abierta correctamente.",
            "sesion": _calcular_sesion(session, sesion_caja),
        }

    @app.post("/pos/caja/movimientos")
    def registrar_movimiento_efectivo_pos(
        data: POSMovimientoEfectivoRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        sesion_caja = _exigir_sesion_abierta(session, current_user, bloquear=True)
        tipo = data.tipo.strip().upper()
        validos = {
            "ENTRADA",
            "RETIRO",
            "GASTO",
            "DEPOSITO",
            "AJUSTE_ENTRADA",
            "AJUSTE_SALIDA",
        }
        if tipo not in validos:
            raise HTTPException(status_code=400, detail="Tipo de movimiento de efectivo inválido.")
        movimiento = POSMovimientoEfectivo(
            id_sesion=int(sesion_caja.id_sesion or 0),
            tipo=tipo,
            monto=_dinero(data.monto),
            motivo=data.motivo.strip(),
            usuario=_usuario_nombre(current_user),
            fecha=mexico_now(),
        )
        session.add(movimiento)
        session.commit()
        return {
            "mensaje": "Movimiento de efectivo registrado.",
            "sesion": _calcular_sesion(session, sesion_caja),
        }

    @app.post("/pos/caja/cerrar")
    def cerrar_caja_pos(
        data: POSCerrarCajaRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        sesion_caja = _exigir_sesion_abierta(session, current_user, bloquear=True)
        resumen = _calcular_sesion(session, sesion_caja)
        contado = _dinero(data.efectivo_contado)
        diferencia = _dinero(contado - resumen["efectivo_esperado"])
        sesion_caja.efectivo_esperado = resumen["efectivo_esperado"]
        sesion_caja.efectivo_contado = contado
        sesion_caja.diferencia = diferencia
        sesion_caja.fecha_cierre = mexico_now()
        sesion_caja.estado = "CERRADA"
        sesion_caja.observaciones = (
            data.observaciones.strip() if data.observaciones else None
        )
        session.add(sesion_caja)
        session.commit()
        session.refresh(sesion_caja)
        return {
            "mensaje": "Caja cerrada correctamente.",
            "sesion": _calcular_sesion(session, sesion_caja),
        }

    @app.get("/pos/caja/sesiones")
    def listar_sesiones_caja_pos(
        limite: int = Query(default=30, ge=1, le=200),
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner', 'viewer'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        statement = select(POSSesionCaja).order_by(POSSesionCaja.fecha_apertura.desc())
        if _usuario_rol(current_user) == "operator":
            statement = statement.where(
                POSSesionCaja.usuario == _usuario_clave(current_user)
            )
        sesiones = session.exec(statement.limit(limite)).all()
        return [_calcular_sesion(session, item) for item in sesiones]

    # ------------------------------------------------------
    # PRODUCTOS
    # ------------------------------------------------------

    @app.get("/pos/productos/buscar")
    def buscar_producto_pos(
        query: str = Query(..., min_length=1),
        limite: int = Query(default=20, ge=1, le=50),
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        valor = query.strip()
        if not valor:
            return []
        exacto = session.exec(
            select(Producto).where(
                or_(Producto.sku == valor, Producto.codigo_barras == valor)
            )
        ).first()
        if exacto:
            productos = [exacto]
        else:
            patron = f"%{valor}%"
            productos = session.exec(
                select(Producto)
                .where(
                    or_(
                        Producto.sku.ilike(patron),
                        Producto.nombre.ilike(patron),
                        Producto.codigo_barras.ilike(patron),
                    )
                )
                .limit(limite)
            ).all()
        return [
            {
                "id_producto": producto.id_producto,
                "sku": producto.sku,
                "codigo_barras": producto.codigo_barras,
                "nombre": producto.nombre,
                "descripcion": producto.descripcion,
                "cantidad": int(producto.cantidad or 0),
                "precio_venta_sugerido": _dinero(producto.precio_venta_sugerido),
                "costo_proveedor": _dinero(producto.costo_proveedor),
                "ubicacion": f"{producto.rack}-{producto.nivel}-{producto.slot}",
                "rack": producto.rack,
                "nivel": producto.nivel,
                "slot": producto.slot,
                "caducidad": producto.caducidad,
            }
            for producto in productos
        ]

    # ------------------------------------------------------
    # VENTAS
    # ------------------------------------------------------

    @app.post("/pos/ventas")
    def crear_venta_pos(
        data: POSVentaRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        sesion_caja = _exigir_sesion_abierta(session, current_user, bloquear=True)
        operacion_id = data.operacion_id.strip()
        duplicado = session.exec(
            select(POSVentaControl).where(
                POSVentaControl.operacion_id == operacion_id
            )
        ).first()
        if duplicado:
            venta_existente = session.get(VentaPOS, duplicado.id_venta)
            if venta_existente:
                respuesta = _serializar_venta(session, venta_existente, True)
                respuesta["mensaje"] = "La operación ya había sido registrada."
                respuesta["duplicada"] = True
                return respuesta

        ahora = mexico_now()
        rol = _usuario_rol(current_user)
        descuento_maximo = 100.0 if rol == "admin" else 10.0
        items_agrupados: Dict[str, POSVentaItemRequest] = {}
        for item in data.items:
            sku = item.sku.strip()
            if not sku:
                raise HTTPException(status_code=400, detail="SKU inválido.")
            if item.descuento_porcentaje > descuento_maximo:
                raise HTTPException(
                    status_code=403,
                    detail=f"Tu rol permite un descuento máximo de {descuento_maximo:.0f}% por producto.",
                )
            existente = items_agrupados.get(sku)
            if existente:
                if abs(existente.descuento_porcentaje - item.descuento_porcentaje) > 0.001:
                    raise HTTPException(
                        status_code=400,
                        detail=f"El SKU {sku} aparece con descuentos diferentes.",
                    )
                existente.cantidad += item.cantidad
            else:
                items_agrupados[sku] = POSVentaItemRequest(
                    sku=sku,
                    cantidad=item.cantidad,
                    descuento_porcentaje=item.descuento_porcentaje,
                )

        calculados: List[Dict[str, Any]] = []
        subtotal_lista = 0.0
        descuento_total = 0.0
        total = 0.0
        costo_total_venta = 0.0

        try:
            for item in items_agrupados.values():
                statement = select(Producto).where(Producto.sku == item.sku)
                try:
                    statement = statement.with_for_update()
                except AttributeError:
                    pass
                producto = session.exec(statement).first()
                if not producto:
                    raise HTTPException(status_code=404, detail=f"Producto {item.sku} no encontrado.")
                disponible = int(producto.cantidad or 0)
                if item.cantidad > disponible:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Stock insuficiente para {producto.nombre}. Disponible: {disponible}.",
                    )
                precio_lista = _dinero(producto.precio_venta_sugerido)
                if precio_lista <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{producto.nombre} no tiene precio de venta configurado.",
                    )
                descuento_unitario = _dinero(precio_lista * item.descuento_porcentaje / 100)
                precio_final = _dinero(precio_lista - descuento_unitario)
                subtotal_linea_lista = _dinero(precio_lista * item.cantidad)
                subtotal_linea = _dinero(precio_final * item.cantidad)
                descuento_linea = _dinero(subtotal_linea_lista - subtotal_linea)
                costo_unitario = _dinero(producto.costo_proveedor)
                costo_linea = _dinero(costo_unitario * item.cantidad)
                ganancia_linea = _dinero(subtotal_linea - costo_linea)
                subtotal_lista = _dinero(subtotal_lista + subtotal_linea_lista)
                descuento_total = _dinero(descuento_total + descuento_linea)
                total = _dinero(total + subtotal_linea)
                costo_total_venta = _dinero(costo_total_venta + costo_linea)
                calculados.append(
                    {
                        "request": item,
                        "producto": producto,
                        "precio_lista": precio_lista,
                        "precio_final": precio_final,
                        "subtotal": subtotal_linea,
                        "costo_unitario": costo_unitario,
                        "costo_total": costo_linea,
                        "ganancia": ganancia_linea,
                    }
                )

            metodos_validos = {"efectivo", "tarjeta", "transferencia"}
            pagos_normalizados: List[Dict[str, Any]] = []
            monto_pagado = 0.0
            efectivo_aplicado = 0.0
            for pago in data.pagos:
                metodo = pago.metodo.strip().lower()
                if metodo not in metodos_validos:
                    raise HTTPException(
                        status_code=400,
                        detail="Método inválido. Usa efectivo, tarjeta o transferencia.",
                    )
                monto = _dinero(pago.monto)
                monto_pagado = _dinero(monto_pagado + monto)
                if metodo == "efectivo":
                    efectivo_aplicado = _dinero(efectivo_aplicado + monto)
                pagos_normalizados.append(
                    {
                        "metodo": metodo,
                        "monto": monto,
                        "referencia": pago.referencia.strip() if pago.referencia else None,
                    }
                )
            if abs(monto_pagado - total) > 0.01:
                raise HTTPException(
                    status_code=400,
                    detail=f"Los pagos suman ${monto_pagado:.2f}, pero el total es ${total:.2f}.",
                )
            recibido = _dinero(
                efectivo_aplicado if data.efectivo_recibido is None else data.efectivo_recibido
            )
            if efectivo_aplicado > 0 and recibido < efectivo_aplicado:
                raise HTTPException(status_code=400, detail="El efectivo recibido no cubre el pago en efectivo.")
            if efectivo_aplicado <= 0:
                recibido = 0
            cambio = _dinero(max(recibido - efectivo_aplicado, 0))
            venta = VentaPOS(
                folio=_folio_venta(ahora),
                usuario=_usuario_nombre(current_user),
                subtotal=subtotal_lista,
                descuento_total=descuento_total,
                total=total,
                costo_total=costo_total_venta,
                ganancia=_dinero(total - costo_total_venta),
                efectivo_recibido=recibido,
                cambio=cambio,
                estado="COMPLETADA",
                fecha=ahora,
            )
            session.add(venta)
            session.flush()
            if venta.id_venta is None:
                raise RuntimeError("No fue posible generar el identificador de venta.")

            control = POSVentaControl(
                id_venta=venta.id_venta,
                id_sesion=int(sesion_caja.id_sesion or 0),
                operacion_id=operacion_id,
                fecha_creacion=ahora,
            )
            session.add(control)

            for calculado in calculados:
                item = calculado["request"]
                producto = calculado["producto"]
                lotes = descontar_lotes_fefo(
                    session=session,
                    sku=producto.sku,
                    cantidad=item.cantidad,
                )
                producto.cantidad = int(producto.cantidad or 0) - item.cantidad
                producto.caducidad = obtener_caducidad_mas_proxima(session, producto.sku)
                producto.ultima_actualizacion = ahora
                session.add(producto)

                detalle = VentaPOSDetalle(
                    id_venta=venta.id_venta,
                    id_producto=producto.id_producto,
                    sku=producto.sku,
                    codigo_barras=producto.codigo_barras,
                    nombre=producto.nombre,
                    cantidad=item.cantidad,
                    precio_lista=calculado["precio_lista"],
                    descuento_porcentaje=item.descuento_porcentaje,
                    precio_unitario_final=calculado["precio_final"],
                    subtotal=calculado["subtotal"],
                    costo_unitario=calculado["costo_unitario"],
                    costo_total=calculado["costo_total"],
                    ganancia=calculado["ganancia"],
                )
                session.add(detalle)
                session.flush()
                if detalle.id_detalle is None:
                    raise RuntimeError("No fue posible crear el detalle de venta.")

                for lote in lotes:
                    session.add(
                        POSVentaLote(
                            id_venta=venta.id_venta,
                            id_detalle=detalle.id_detalle,
                            id_lote=lote.get("id_lote"),
                            sku=producto.sku,
                            cantidad=int(lote.get("cantidad_descontada") or 0),
                            cantidad_restaurada=0,
                        )
                    )

                ubicacion = f"{producto.rack}-{producto.nivel}-{producto.slot}"
                movimiento = Movimiento(
                    accion="Egreso",
                    sku=producto.sku,
                    producto=producto.nombre,
                    cantidad=item.cantidad,
                    ubicacion=ubicacion,
                    usuario=_usuario_nombre(current_user),
                    fecha=ahora,
                    costo_proveedor=calculado["costo_unitario"],
                    precio_venta=calculado["precio_final"],
                    ingreso_total=calculado["subtotal"],
                    costo_total=calculado["costo_total"],
                    ganancia=calculado["ganancia"],
                )
                session.add(movimiento)
                session.flush()
                if movimiento.id_mov is not None:
                    session.add(
                        POSVentaMovimiento(
                            id_venta=venta.id_venta,
                            id_detalle=detalle.id_detalle,
                            id_movimiento=movimiento.id_mov,
                        )
                    )

            for pago in pagos_normalizados:
                session.add(
                    VentaPOSPago(
                        id_venta=venta.id_venta,
                        metodo=pago["metodo"],
                        monto=pago["monto"],
                        referencia=pago["referencia"],
                    )
                )
            session.commit()
            session.refresh(venta)
            respuesta = _serializar_venta(session, venta, True)
            respuesta["mensaje"] = "Venta registrada correctamente."
            respuesta["duplicada"] = False
            return respuesta
        except HTTPException:
            session.rollback()
            raise
        except Exception as error:
            session.rollback()
            print(f"❌ Error creando venta POS: {error}")
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo registrar la venta: {str(error)}",
            ) from error

    @app.get("/pos/ventas")
    def listar_ventas_pos(
        limite: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner', 'viewer'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        ventas = session.exec(
            select(VentaPOS).order_by(VentaPOS.fecha.desc()).limit(limite)
        ).all()
        return [_serializar_venta(session, venta, False) for venta in ventas]

    @app.get("/pos/ventas/{id_venta}")
    def obtener_venta_pos(
        id_venta: int,
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner', 'viewer'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        venta = session.get(VentaPOS, id_venta)
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada.")
        return _serializar_venta(session, venta, True)

    # ------------------------------------------------------
    # CANCELACIONES Y DEVOLUCIONES
    # ------------------------------------------------------

    @app.post("/pos/ventas/{id_venta}/cancelar")
    def cancelar_venta_pos(
        id_venta: int,
        data: POSCancelarVentaRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        venta = session.get(VentaPOS, id_venta)
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada.")
        if venta.estado != "COMPLETADA":
            raise HTTPException(status_code=409, detail="La venta ya no está disponible para cancelación.")
        control = _venta_control(session, id_venta)
        if not control:
            raise HTTPException(status_code=409, detail="Esta venta es anterior a Caja Profesional y no puede cancelarse automáticamente.")
        sesion_caja = session.get(POSSesionCaja, control.id_sesion)
        if not sesion_caja or sesion_caja.estado != "ABIERTA":
            raise HTTPException(
                status_code=409,
                detail="Solo puedes cancelar ventas antes de cerrar la sesión de caja donde se registraron.",
            )
        devoluciones = session.exec(
            select(POSDevolucion).where(POSDevolucion.id_venta == id_venta)
        ).all()
        if devoluciones:
            raise HTTPException(status_code=409, detail="No puedes cancelar una venta que ya tiene devoluciones.")

        try:
            detalles = session.exec(
                select(VentaPOSDetalle).where(VentaPOSDetalle.id_venta == id_venta)
            ).all()
            ahora = mexico_now()
            for detalle in detalles:
                producto = session.get(Producto, detalle.id_producto) if detalle.id_producto else None
                if producto is None:
                    producto = session.exec(select(Producto).where(Producto.sku == detalle.sku)).first()
                if producto is None:
                    raise HTTPException(status_code=409, detail=f"No se encontró el producto {detalle.sku} para restaurar inventario.")
                _restaurar_lotes(
                    session=session,
                    detalle=detalle,
                    cantidad=int(detalle.cantidad or 0),
                    ProductoLote=ProductoLote,
                    mexico_now=mexico_now,
                )
                producto.cantidad = int(producto.cantidad or 0) + int(detalle.cantidad or 0)
                producto.caducidad = obtener_caducidad_mas_proxima(session, producto.sku)
                producto.ultima_actualizacion = ahora
                session.add(producto)

                vinculo = session.exec(
                    select(POSVentaMovimiento).where(
                        POSVentaMovimiento.id_detalle == detalle.id_detalle
                    )
                ).first()
                if vinculo:
                    original = session.get(Movimiento, vinculo.id_movimiento)
                    if original:
                        original.cantidad = 0
                        original.ingreso_total = 0
                        original.costo_total = 0
                        original.ganancia = 0
                        session.add(original)
                session.add(
                    Movimiento(
                        accion="Cancelación POS",
                        sku=detalle.sku,
                        producto=detalle.nombre,
                        cantidad=int(detalle.cantidad or 0),
                        ubicacion=f"{producto.rack}-{producto.nivel}-{producto.slot}",
                        usuario=_usuario_nombre(current_user),
                        fecha=ahora,
                        costo_proveedor=detalle.costo_unitario,
                        precio_venta=detalle.precio_unitario_final,
                        ingreso_total=0,
                        costo_total=0,
                        ganancia=0,
                    )
                )

            venta.estado = "CANCELADA"
            control.anulada_por = _usuario_nombre(current_user)
            control.motivo_anulacion = data.motivo.strip()
            control.fecha_anulacion = ahora
            session.add(venta)
            session.add(control)
            session.commit()
            respuesta = _serializar_venta(session, venta, True)
            respuesta["mensaje"] = "Venta cancelada e inventario restaurado."
            return respuesta
        except HTTPException:
            session.rollback()
            raise
        except Exception as error:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"No se pudo cancelar la venta: {error}") from error

    @app.post("/pos/ventas/{id_venta}/devoluciones")
    def devolver_venta_pos(
        id_venta: int,
        data: POSDevolucionRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'owner'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        venta = session.get(VentaPOS, id_venta)
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada.")
        if venta.estado != "COMPLETADA":
            raise HTTPException(status_code=409, detail="Solo se permiten devoluciones de ventas completadas.")
        control = _venta_control(session, id_venta)
        if not control:
            raise HTTPException(
                status_code=409,
                detail="Esta venta es anterior a Caja Profesional y no puede devolverse automáticamente.",
            )
        sesion_caja = session.get(POSSesionCaja, control.id_sesion)
        if not sesion_caja or sesion_caja.estado != "ABIERTA":
            raise HTTPException(
                status_code=409,
                detail="Solo puedes devolver productos antes de cerrar la sesión de caja donde se vendieron.",
            )
        metodo = data.metodo_reembolso.strip().lower()
        if metodo not in {"efectivo", "tarjeta", "transferencia"}:
            raise HTTPException(status_code=400, detail="Método de reembolso inválido.")

        solicitados: Dict[int, int] = {}
        for item in data.items:
            solicitados[item.id_detalle] = solicitados.get(item.id_detalle, 0) + item.cantidad

        try:
            detalles_calculados: List[Dict[str, Any]] = []
            total_reembolso = 0.0
            for id_detalle, cantidad in solicitados.items():
                detalle = session.get(VentaPOSDetalle, id_detalle)
                if not detalle or detalle.id_venta != id_venta:
                    raise HTTPException(status_code=404, detail="Detalle de venta no encontrado.")
                ya_devuelto = _total_devuelto_detalle(session, id_detalle)
                disponible = int(detalle.cantidad or 0) - ya_devuelto
                if cantidad > disponible:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Solo puedes devolver {disponible} unidad(es) de {detalle.nombre}.",
                    )
                subtotal = _dinero(detalle.precio_unitario_final * cantidad)
                total_reembolso = _dinero(total_reembolso + subtotal)
                detalles_calculados.append(
                    {"detalle": detalle, "cantidad": cantidad, "subtotal": subtotal}
                )

            ahora = mexico_now()
            devolucion = POSDevolucion(
                folio=_folio_devolucion(ahora),
                id_venta=id_venta,
                id_sesion=int(sesion_caja.id_sesion or 0),
                usuario=_usuario_nombre(current_user),
                motivo=data.motivo.strip(),
                metodo_reembolso=metodo,
                monto=total_reembolso,
                estado="COMPLETADA",
                fecha=ahora,
            )
            session.add(devolucion)
            session.flush()
            if devolucion.id_devolucion is None:
                raise RuntimeError("No fue posible crear la devolución.")

            for calculado in detalles_calculados:
                detalle = calculado["detalle"]
                cantidad = calculado["cantidad"]
                producto = session.get(Producto, detalle.id_producto) if detalle.id_producto else None
                if producto is None:
                    producto = session.exec(select(Producto).where(Producto.sku == detalle.sku)).first()
                if producto is None:
                    raise HTTPException(status_code=409, detail=f"No se encontró el producto {detalle.sku} para restaurar inventario.")
                _restaurar_lotes(
                    session=session,
                    detalle=detalle,
                    cantidad=cantidad,
                    ProductoLote=ProductoLote,
                    mexico_now=mexico_now,
                )
                producto.cantidad = int(producto.cantidad or 0) + cantidad
                producto.caducidad = obtener_caducidad_mas_proxima(session, producto.sku)
                producto.ultima_actualizacion = ahora
                session.add(producto)
                session.add(
                    POSDevolucionDetalle(
                        id_devolucion=devolucion.id_devolucion,
                        id_detalle_venta=int(detalle.id_detalle or 0),
                        id_producto=detalle.id_producto,
                        sku=detalle.sku,
                        nombre=detalle.nombre,
                        cantidad=cantidad,
                        precio_unitario=_dinero(detalle.precio_unitario_final),
                        subtotal=calculado["subtotal"],
                    )
                )
                session.flush()
                _actualizar_movimiento_neto(
                    session=session,
                    detalle=detalle,
                    Movimiento=Movimiento,
                )
                session.add(
                    Movimiento(
                        accion="Devolución POS",
                        sku=detalle.sku,
                        producto=detalle.nombre,
                        cantidad=cantidad,
                        ubicacion=f"{producto.rack}-{producto.nivel}-{producto.slot}",
                        usuario=_usuario_nombre(current_user),
                        fecha=ahora,
                        costo_proveedor=detalle.costo_unitario,
                        precio_venta=detalle.precio_unitario_final,
                        ingreso_total=0,
                        costo_total=0,
                        ganancia=0,
                    )
                )

            session.commit()
            return {
                "mensaje": "Devolución registrada e inventario restaurado.",
                "id_devolucion": devolucion.id_devolucion,
                "folio": devolucion.folio,
                "monto": _dinero(devolucion.monto),
                "metodo_reembolso": devolucion.metodo_reembolso,
                "venta": _serializar_venta(session, venta, True),
            }
        except HTTPException:
            session.rollback()
            raise
        except Exception as error:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"No se pudo registrar la devolución: {error}") from error

    @app.get("/pos/devoluciones")
    def listar_devoluciones_pos(
        limite: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),

        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),):
        _rn_bind_empresa(session, current_user, rn_empresa_id, allowed_roles={'admin', 'operator', 'owner', 'viewer'})  # RACKNOVA_MULTIEMPRESA_FASE2_LOCAL_FIRST
        _exigir_pos_habilitado(session)
        devoluciones = session.exec(
            select(POSDevolucion).order_by(POSDevolucion.fecha.desc()).limit(limite)
        ).all()
        return [
            {
                "id_devolucion": item.id_devolucion,
                "folio": item.folio,
                "id_venta": item.id_venta,
                "id_sesion": item.id_sesion,
                "usuario": item.usuario,
                "motivo": item.motivo,
                "metodo_reembolso": item.metodo_reembolso,
                "monto": _dinero(item.monto),
                "estado": item.estado,
                "fecha": item.fecha,
            }
            for item in devoluciones
        ]
