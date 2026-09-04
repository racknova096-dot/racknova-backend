from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import text as sa_text
from sqlmodel import Field, Session, SQLModel, select

import multiempresa_tenant as rn_tenant


class Proveedor(SQLModel, table=True):
    __tablename__ = "proveedor"

    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)
    id_proveedor: UUID = Field(default_factory=uuid4, primary_key=True)
    nombre: str = Field(index=True, max_length=160)
    contacto: Optional[str] = Field(default=None, max_length=160)
    telefono: Optional[str] = Field(default=None, max_length=60)
    whatsapp: Optional[str] = Field(default=None, max_length=60)
    email: Optional[str] = Field(default=None, max_length=180)
    notas: Optional[str] = Field(default=None)
    tiempo_entrega_dias: int = Field(default=0)
    activo: bool = Field(default=True, index=True)
    fecha_creacion: datetime
    fecha_actualizacion: datetime

    sync_uuid: UUID = Field(default_factory=uuid4, nullable=False, index=True)
    sync_revision: int = Field(default=0, nullable=False)
    sync_updated_at: Optional[datetime] = Field(default=None, nullable=True)
    sync_origen_nodo: Optional[str] = Field(default=None, nullable=True, max_length=120)


class ProductoProveedor(SQLModel, table=True):
    __tablename__ = "producto_proveedor"

    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)
    id_relacion: UUID = Field(default_factory=uuid4, primary_key=True)
    sku: str = Field(index=True, max_length=120)
    id_proveedor: UUID = Field(index=True)
    es_principal: bool = Field(default=True, index=True)
    costo_ultimo: float = Field(default=0)
    fecha_creacion: datetime
    fecha_actualizacion: datetime

    sync_uuid: UUID = Field(default_factory=uuid4, nullable=False, index=True)
    sync_revision: int = Field(default=0, nullable=False)
    sync_updated_at: Optional[datetime] = Field(default=None, nullable=True)
    sync_origen_nodo: Optional[str] = Field(default=None, nullable=True, max_length=120)


class ProveedorCreate(BaseModel):
    nombre: str = PydanticField(min_length=2, max_length=160)
    contacto: Optional[str] = PydanticField(default=None, max_length=160)
    telefono: Optional[str] = PydanticField(default=None, max_length=60)
    whatsapp: Optional[str] = PydanticField(default=None, max_length=60)
    email: Optional[str] = PydanticField(default=None, max_length=180)
    notas: Optional[str] = PydanticField(default=None, max_length=1000)
    tiempo_entrega_dias: int = PydanticField(default=0, ge=0, le=365)


class ProveedorUpdate(BaseModel):
    nombre: Optional[str] = PydanticField(default=None, min_length=2, max_length=160)
    contacto: Optional[str] = PydanticField(default=None, max_length=160)
    telefono: Optional[str] = PydanticField(default=None, max_length=60)
    whatsapp: Optional[str] = PydanticField(default=None, max_length=60)
    email: Optional[str] = PydanticField(default=None, max_length=180)
    notas: Optional[str] = PydanticField(default=None, max_length=1000)
    tiempo_entrega_dias: Optional[int] = PydanticField(default=None, ge=0, le=365)
    activo: Optional[bool] = None


class ProductoProveedorSet(BaseModel):
    id_proveedor: UUID
    costo_ultimo: float = PydanticField(default=0, ge=0)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _proveedor_payload(row: Proveedor) -> dict[str, Any]:
    return {
        "id_proveedor": str(row.id_proveedor),
        "nombre": row.nombre,
        "contacto": row.contacto,
        "telefono": row.telefono,
        "whatsapp": row.whatsapp,
        "email": row.email,
        "notas": row.notas,
        "tiempo_entrega_dias": int(row.tiempo_entrega_dias or 0),
        "activo": bool(row.activo),
        "fecha_creacion": row.fecha_creacion,
        "fecha_actualizacion": row.fecha_actualizacion,
    }


def _relacion_payload(row: ProductoProveedor, proveedor: Optional[Proveedor]) -> dict[str, Any]:
    return {
        "id_relacion": str(row.id_relacion),
        "sku": row.sku,
        "id_proveedor": str(row.id_proveedor),
        "proveedor": proveedor.nombre if proveedor else None,
        "es_principal": bool(row.es_principal),
        "costo_ultimo": float(row.costo_ultimo or 0),
        "fecha_actualizacion": row.fecha_actualizacion,
    }


MEXICO_TZ = ZoneInfo("America/Mexico_City")


def mexico_now() -> datetime:
    return datetime.now(MEXICO_TZ).replace(tzinfo=None)


def registrar_modulo_compras(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    def bind(
        session: Session,
        current_user: Any,
        empresa: Optional[str],
        *,
        write: bool,
    ) -> str:
        selected = rn_tenant.bind_empresa(
            session,
            current_user,
            empresa,
            allowed_roles=(
                {"owner", "admin", "operator"}
                if write
                else {"owner", "admin", "operator", "viewer"}
            ),
        )
        return str(selected["id_empresa"])

    @app.get("/compras/proveedores", tags=["Compras"])
    def listar_proveedores(
        incluir_inactivos: bool = Query(default=False),
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind(session, current_user, rn_empresa_id, write=False)
        statement = select(Proveedor)
        if not incluir_inactivos:
            statement = statement.where(Proveedor.activo == True)
        rows = session.exec(statement).all()
        rows = sorted(rows, key=lambda item: item.nombre.lower())
        return [_proveedor_payload(row) for row in rows]

    @app.post("/compras/proveedores", tags=["Compras"])
    def crear_proveedor(
        data: ProveedorCreate,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind(session, current_user, rn_empresa_id, write=True)
        nombre = str(data.nombre or "").strip()
        existente = session.exec(
            select(Proveedor).where(Proveedor.nombre.ilike(nombre))
        ).first()
        if existente:
            if not existente.activo:
                existente.activo = True
                existente.fecha_actualizacion = mexico_now()
                session.add(existente)
                session.commit()
                session.refresh(existente)
                return _proveedor_payload(existente)
            raise HTTPException(status_code=409, detail="Ya existe un proveedor con ese nombre.")

        row = Proveedor(
            nombre=nombre,
            contacto=_clean(data.contacto),
            telefono=_clean(data.telefono),
            whatsapp=_clean(data.whatsapp),
            email=_clean(data.email),
            notas=_clean(data.notas),
            tiempo_entrega_dias=int(data.tiempo_entrega_dias or 0),
            activo=True,
            fecha_creacion=mexico_now(),
            fecha_actualizacion=mexico_now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _proveedor_payload(row)

    @app.put("/compras/proveedores/{id_proveedor}", tags=["Compras"])
    def actualizar_proveedor(
        id_proveedor: UUID,
        data: ProveedorUpdate,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind(session, current_user, rn_empresa_id, write=True)
        row = session.get(Proveedor, id_proveedor)
        if not row:
            raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

        if data.nombre is not None:
            nombre = data.nombre.strip()
            duplicado = session.exec(
                select(Proveedor).where(
                    (Proveedor.nombre.ilike(nombre))
                    & (Proveedor.id_proveedor != id_proveedor)
                )
            ).first()
            if duplicado:
                raise HTTPException(status_code=409, detail="Ya existe otro proveedor con ese nombre.")
            row.nombre = nombre

        for campo in ("contacto", "telefono", "whatsapp", "email", "notas"):
            value = getattr(data, campo)
            if value is not None:
                setattr(row, campo, _clean(value))

        if data.tiempo_entrega_dias is not None:
            row.tiempo_entrega_dias = int(data.tiempo_entrega_dias)
        if data.activo is not None:
            row.activo = bool(data.activo)

        row.fecha_actualizacion = mexico_now()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _proveedor_payload(row)

    @app.delete("/compras/proveedores/{id_proveedor}", tags=["Compras"])
    def desactivar_proveedor(
        id_proveedor: UUID,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind(session, current_user, rn_empresa_id, write=True)
        row = session.get(Proveedor, id_proveedor)
        if not row:
            raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
        row.activo = False
        row.fecha_actualizacion = mexico_now()
        session.add(row)

        relaciones = session.exec(
            select(ProductoProveedor).where(
                ProductoProveedor.id_proveedor == id_proveedor
            )
        ).all()
        for relacion in relaciones:
            relacion.es_principal = False
            relacion.fecha_actualizacion = mexico_now()
            session.add(relacion)

        session.commit()
        return {"ok": True, "mensaje": "Proveedor desactivado."}

    @app.get("/compras/productos/{sku}/proveedores", tags=["Compras"])
    def proveedores_producto(
        sku: str,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        bind(session, current_user, rn_empresa_id, write=False)
        relaciones = session.exec(
            select(ProductoProveedor).where(ProductoProveedor.sku == sku.strip())
        ).all()
        resultado = []
        for relacion in relaciones:
            proveedor = session.get(Proveedor, relacion.id_proveedor)
            if proveedor and proveedor.activo:
                resultado.append(_relacion_payload(relacion, proveedor))
        return sorted(
            resultado,
            key=lambda item: (
                not item["es_principal"],
                (item["proveedor"] or "").lower(),
            ),
        )

    @app.put("/compras/productos/{sku}/proveedor-principal", tags=["Compras"])
    def asignar_proveedor_principal(
        sku: str,
        data: ProductoProveedorSet,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        empresa_id = bind(session, current_user, rn_empresa_id, write=True)
        sku_clean = sku.strip()
        proveedor = session.get(Proveedor, data.id_proveedor)
        if not proveedor or not proveedor.activo:
            raise HTTPException(status_code=404, detail="Proveedor activo no encontrado.")

        producto = session.connection().execute(
            sa_text(
                """
                SELECT sku
                FROM producto
                WHERE empresa_id = CAST(:empresa_id AS UUID)
                  AND sku = :sku
                LIMIT 1
                """
            ),
            {"empresa_id": empresa_id, "sku": sku_clean},
        ).first()
        if not producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado en inventario.")

        relaciones = session.exec(
            select(ProductoProveedor).where(ProductoProveedor.sku == sku_clean)
        ).all()
        relacion_actual: Optional[ProductoProveedor] = None
        for relacion in relaciones:
            if relacion.id_proveedor == data.id_proveedor:
                relacion_actual = relacion
                relacion.es_principal = True
                relacion.costo_ultimo = float(data.costo_ultimo or relacion.costo_ultimo or 0)
            else:
                relacion.es_principal = False
            relacion.fecha_actualizacion = mexico_now()
            session.add(relacion)

        if relacion_actual is None:
            relacion_actual = ProductoProveedor(
                sku=sku_clean,
                id_proveedor=data.id_proveedor,
                es_principal=True,
                costo_ultimo=float(data.costo_ultimo or 0),
                fecha_creacion=mexico_now(),
                fecha_actualizacion=mexico_now(),
            )
            session.add(relacion_actual)

        session.commit()
        session.refresh(relacion_actual)
        return _relacion_payload(relacion_actual, proveedor)

    @app.get("/compras/reabastecimiento", tags=["Compras"])
    def reabastecimiento(
        solo_criticos: bool = Query(default=True),
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        empresa_id = bind(session, current_user, rn_empresa_id, write=False)
        comparador = "AND p.cantidad <= p.stock_minimo" if solo_criticos else ""
        rows = session.connection().execute(
            sa_text(
                f"""
                SELECT
                    p.sku,
                    p.nombre,
                    COALESCE(p.cantidad, 0) AS stock_actual,
                    COALESCE(p.stock_minimo, 10) AS stock_minimo,
                    COALESCE(p.stock_alto, GREATEST(COALESCE(p.stock_minimo, 10) * 3, 1)) AS stock_objetivo,
                    COALESCE(NULLIF(pp.costo_ultimo, 0), p.costo_proveedor, 0) AS costo_unitario,
                    pp.id_proveedor,
                    pr.nombre AS proveedor,
                    pr.contacto,
                    pr.telefono,
                    pr.whatsapp,
                    pr.email,
                    COALESCE(pr.tiempo_entrega_dias, 0) AS tiempo_entrega_dias
                FROM producto p
                LEFT JOIN producto_proveedor pp
                  ON pp.empresa_id = p.empresa_id
                 AND pp.sku = p.sku
                 AND pp.es_principal = TRUE
                LEFT JOIN proveedor pr
                  ON pr.empresa_id = p.empresa_id
                 AND pr.id_proveedor = pp.id_proveedor
                 AND pr.activo = TRUE
                WHERE p.empresa_id = CAST(:empresa_id AS UUID)
                  {comparador}
                ORDER BY
                    CASE WHEN pr.nombre IS NULL THEN 1 ELSE 0 END,
                    pr.nombre,
                    p.cantidad,
                    p.nombre
                """
            ),
            {"empresa_id": empresa_id},
        ).mappings().all()

        grupos: dict[str, dict[str, Any]] = {}
        total_productos = 0
        total_estimado = 0.0

        for raw in rows:
            row = dict(raw)
            stock_actual = max(int(row["stock_actual"] or 0), 0)
            stock_minimo = max(int(row["stock_minimo"] or 10), 0)
            stock_objetivo = max(int(row["stock_objetivo"] or stock_minimo * 3), stock_minimo)
            cantidad_sugerida = max(stock_objetivo - stock_actual, 0)
            costo_unitario = float(row["costo_unitario"] or 0)
            subtotal = round(cantidad_sugerida * costo_unitario, 2)
            proveedor_id = str(row["id_proveedor"]) if row["id_proveedor"] else None
            key = proveedor_id or "__sin_proveedor__"

            if key not in grupos:
                grupos[key] = {
                    "id_proveedor": proveedor_id,
                    "proveedor": row["proveedor"] or "Sin proveedor",
                    "contacto": row["contacto"],
                    "telefono": row["telefono"],
                    "whatsapp": row["whatsapp"],
                    "email": row["email"],
                    "tiempo_entrega_dias": int(row["tiempo_entrega_dias"] or 0),
                    "productos": [],
                    "total_productos": 0,
                    "total_unidades_sugeridas": 0,
                    "total_estimado": 0.0,
                }

            item = {
                "sku": row["sku"],
                "nombre": row["nombre"],
                "stock_actual": stock_actual,
                "stock_minimo": stock_minimo,
                "stock_objetivo": stock_objetivo,
                "cantidad_sugerida": cantidad_sugerida,
                "costo_unitario": round(costo_unitario, 4),
                "subtotal_estimado": subtotal,
            }
            grupos[key]["productos"].append(item)
            grupos[key]["total_productos"] += 1
            grupos[key]["total_unidades_sugeridas"] += cantidad_sugerida
            grupos[key]["total_estimado"] = round(
                grupos[key]["total_estimado"] + subtotal, 2
            )
            total_productos += 1
            total_estimado += subtotal

        return {
            "criterio": "stock_actual <= stock_minimo" if solo_criticos else "todos",
            "formula_sugerida": "max(stock_objetivo - stock_actual, 0)",
            "total_productos": total_productos,
            "total_proveedores": len(
                [g for g in grupos.values() if g["id_proveedor"]]
            ),
            "total_estimado": round(total_estimado, 2),
            "grupos": list(grupos.values()),
        }
