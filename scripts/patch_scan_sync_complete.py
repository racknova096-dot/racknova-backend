from pathlib import Path

BRANCH_MARKER = "RACKNOVA_SCAN_SYNC_COMPLETE_20260903"


def patch_prefixes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if BRANCH_MARKER in text:
        return

    anchor = '    "lote",\n)\n'
    replacement = '    "lote",\n    "racknova_scan_",\n    "racknova_ubicacion_",\n)\n'
    if anchor not in text:
        raise SystemExit(f"COMMERCIAL_PREFIXES anchor not found in {path}")
    text = text.replace(anchor, replacement, 1)

    if path.name == "racknova_sync_worker.py":
        sql_anchor = "                      OR t.table_name LIKE 'lote%'\n                  )\n"
        sql_replacement = (
            "                      OR t.table_name LIKE 'lote%'\n"
            "                      OR t.table_name LIKE 'racknova_scan_%'\n"
            "                      OR t.table_name LIKE 'racknova_ubicacion_%'\n"
            "                  )\n"
        )
        if sql_anchor not in text:
            raise SystemExit("trigger table filter anchor not found")
        text = text.replace(sql_anchor, sql_replacement, 1)

    text += f"\n# {BRANCH_MARKER}\n"
    path.write_text(text, encoding="utf-8")


def patch_main(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if BRANCH_MARKER in text:
        return

    migration_anchor = '''def ejecutar_migraciones_ligeras():\n    with Session(engine) as session:\n'''
    migration_block = '''def ejecutar_migraciones_ligeras():\n    with Session(engine) as session:\n        # RackNova Scan Fase 3: las tablas pudieron existir antes de incorporar\n        # identidad/revision B3. create_all() no altera tablas existentes, por\n        # eso completamos el esquema de forma idempotente en PostgreSQL.\n        if es_postgres():\n            for tabla_scan in (\n                "racknova_scan_configuracion",\n                "racknova_ubicacion_identidad",\n            ):\n                agregar_columna_si_falta(\n                    session, tabla_scan, "sync_uuid",\n                    "UUID NOT NULL DEFAULT gen_random_uuid()",\n                )\n                agregar_columna_si_falta(\n                    session, tabla_scan, "sync_revision",\n                    "BIGINT NOT NULL DEFAULT 0",\n                )\n                agregar_columna_si_falta(\n                    session, tabla_scan, "sync_updated_at",\n                    "TIMESTAMP NULL",\n                )\n                agregar_columna_si_falta(\n                    session, tabla_scan, "sync_origen_nodo",\n                    "VARCHAR(120) NULL",\n                )\n\n'''
    if migration_anchor not in text:
        raise SystemExit("migration function anchor not found")
    text = text.replace(migration_anchor, migration_block, 1)

    indices_anchor = '''    indices = [\n        ("producto", "idx_producto_sku_fast", ["sku"]),\n'''
    indices_block = '''    indices = [\n        (\n            "racknova_scan_configuracion",\n            "idx_racknova_scan_config_sync",\n            ["empresa_id", "sync_uuid"],\n        ),\n        (\n            "racknova_ubicacion_identidad",\n            "idx_racknova_ubicacion_sync",\n            ["empresa_id", "sync_uuid"],\n        ),\n        ("producto", "idx_producto_sku_fast", ["sku"]),\n'''
    if indices_anchor not in text:
        raise SystemExit("indices anchor not found")
    text = text.replace(indices_anchor, indices_block, 1)

    text += f"\n# {BRANCH_MARKER}\n"
    path.write_text(text, encoding="utf-8")


patch_prefixes(Path("racknova_sync_capture.py"))
patch_prefixes(Path("racknova_sync_worker.py"))
patch_main(Path("main.py"))
print("RackNova Scan config/location sync patch applied")
