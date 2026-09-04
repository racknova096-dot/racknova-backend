from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"Anchor not found: {label}")
    return text.replace(old, new, 1)


main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")

# 1) Producto keeps legacy rack/nivel/slot for compatibility, but RNLOC becomes
# the canonical free physical location for new entries.
main = replace_once(
    main,
    '    codigo_barras: Optional[str] = Field(default=None, index=True)\n    cantidad: int = 0\n',
    '    codigo_barras: Optional[str] = Field(default=None, index=True)\n    ubicacion_codigo: Optional[str] = Field(default=None, index=True)\n    cantidad: int = 0\n',
    "Producto.ubicacion_codigo",
)

# 2) Existing databases receive the nullable column idempotently.
main = replace_once(
    main,
    '''        agregar_columna_si_falta(\n            session,\n            "producto",\n            "codigo_barras",\n            "VARCHAR(128) NULL",\n        )\n\n''',
    '''        agregar_columna_si_falta(\n            session,\n            "producto",\n            "codigo_barras",\n            "VARCHAR(128) NULL",\n        )\n\n        agregar_columna_si_falta(\n            session,\n            "producto",\n            "ubicacion_codigo",\n            "VARCHAR(160) NULL",\n        )\n\n''',
    "migration producto.ubicacion_codigo",
)

main = replace_once(
    main,
    '        ("producto", "idx_producto_ubicacion_fast", ["rack", "nivel", "slot"]),\n',
    '        ("producto", "idx_producto_ubicacion_fast", ["rack", "nivel", "slot"]),\n        ("producto", "idx_producto_ubicacion_codigo_fast", ["ubicacion_codigo"]),\n',
    "free location index",
)

# 3) Normalize RNLOC coming from clients.
main = replace_once(
    main,
    '        producto.codigo_barras = normalizar_texto(producto.codigo_barras) or None\n\n        producto.rack = normalizar_texto(producto.rack)\n',
    '        producto.codigo_barras = normalizar_texto(producto.codigo_barras) or None\n        producto.ubicacion_codigo = normalizar_texto(producto.ubicacion_codigo) or None\n\n        producto.rack = normalizar_texto(producto.rack)\n',
    "normalize free location",
)

# 4) Restock may migrate a legacy product to RNLOC, or keep the existing RNLOC.
main = replace_once(
    main,
    '''            producto_existente.codigo_barras = (\n                producto.codigo_barras or producto_existente.codigo_barras\n            )\n            producto_existente.ultima_actualizacion = mexico_now()\n''',
    '''            producto_existente.codigo_barras = (\n                producto.codigo_barras or producto_existente.codigo_barras\n            )\n            if producto.ubicacion_codigo:\n                producto_existente.ubicacion_codigo = producto.ubicacion_codigo\n            producto_existente.ultima_actualizacion = mexico_now()\n''',
    "restock RNLOC migration",
)

# 5) New products no longer require a Rack/Nivel/Slot when they have RNLOC.
old_new_product = '''        # ======================================================\n        # PRODUCTO NUEVO\n        # ======================================================\n        if not producto.rack or not producto.nivel or not producto.slot:\n            raise HTTPException(\n                status_code=400,\n                detail="Rack, nivel y slot son obligatorios para un producto nuevo.",\n            )\n\n        producto_en_slot = buscar_producto_por_ubicacion(\n            session,\n            producto.rack,\n            producto.nivel,\n            producto.slot,\n        )\n\n        if producto_en_slot:\n            raise HTTPException(\n                status_code=400,\n                detail=(\n                    f"El slot {producto.rack}-{producto.nivel}-{producto.slot} "\n                    "ya contiene un producto."\n                ),\n            )\n\n'''
new_new_product = '''        # ======================================================\n        # PRODUCTO NUEVO\n        # ======================================================\n        # RNLOC es una ubicación física libre: puede ser anaquel, refrigerador,\n        # cajón, piso, tarima, rack o cualquier lugar que el cliente elija.\n        # Rack/Nivel/Slot quedan únicamente como compatibilidad histórica.\n        if producto.ubicacion_codigo:\n            producto.rack = producto.rack or "LIBRE"\n            producto.nivel = producto.nivel or "0"\n            producto.slot = producto.slot or "0"\n        else:\n            if not producto.rack or not producto.nivel or not producto.slot:\n                raise HTTPException(\n                    status_code=400,\n                    detail=(\n                        "El producto nuevo necesita una ubicación RNLOC o una "\n                        "ubicación heredada Rack/Nivel/Slot."\n                    ),\n                )\n\n            producto_en_slot = buscar_producto_por_ubicacion(\n                session,\n                producto.rack,\n                producto.nivel,\n                producto.slot,\n            )\n\n            if producto_en_slot:\n                raise HTTPException(\n                    status_code=400,\n                    detail=(\n                        f"El slot {producto.rack}-{producto.nivel}-{producto.slot} "\n                        "ya contiene un producto."\n                    ),\n                )\n\n'''
main = replace_once(main, old_new_product, new_new_product, "new product free location")

# 6) Editing/reassignment can carry RNLOC too.
main = replace_once(
    main,
    '''        db_producto.codigo_barras = (\n            normalizar_texto(updated.codigo_barras)\n            or db_producto.codigo_barras\n        )\n\n        db_producto.stock_minimo = normalizar_stock_minimo(updated.stock_minimo)\n''',
    '''        db_producto.codigo_barras = (\n            normalizar_texto(updated.codigo_barras)\n            or db_producto.codigo_barras\n        )\n        if normalizar_texto(updated.ubicacion_codigo):\n            db_producto.ubicacion_codigo = normalizar_texto(updated.ubicacion_codigo)\n\n        db_producto.stock_minimo = normalizar_stock_minimo(updated.stock_minimo)\n''',
    "update product RNLOC",
)

# 7) IA/inventory summaries prefer the universal location code.
main = main.replace(
    '''                "ubicacion": (\n                    f"{producto.rack}-"\n                    f"{producto.nivel}-"\n                    f"{producto.slot}"\n                ),''',
    '''                "ubicacion": (\n                    producto.ubicacion_codigo\n                    or f"{producto.rack}-{producto.nivel}-{producto.slot}"\n                ),''',
)

main_path.write_text(main, encoding="utf-8")

# POS responses and movements should also expose the universal location.
pos_path = Path("pos_module.py")
pos = pos_path.read_text(encoding="utf-8")
pos = pos.replace(
    '"ubicacion": f"{producto.rack}-{producto.nivel}-{producto.slot}"',
    '"ubicacion": getattr(producto, "ubicacion_codigo", None) or f"{producto.rack}-{producto.nivel}-{producto.slot}"',
)
pos = pos.replace(
    'ubicacion = f"{producto.rack}-{producto.nivel}-{producto.slot}"',
    'ubicacion = getattr(producto, "ubicacion_codigo", None) or f"{producto.rack}-{producto.nivel}-{producto.slot}"',
)
pos_path.write_text(pos, encoding="utf-8")

print("Free-location backend patch applied")
