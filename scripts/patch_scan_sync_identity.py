from pathlib import Path

# scan_control_module.py
path = Path("scan_control_module.py")
text = path.read_text(encoding="utf-8")
if "_rn_current_empresa_id" not in text:
    text = text.replace(
        "from multiempresa_tenant import bind_empresa as _rn_bind_empresa\n",
        "from multiempresa_tenant import bind_empresa as _rn_bind_empresa\n"
        "from multiempresa_tenant import current_empresa_id as _rn_current_empresa_id\n",
        1,
    )

old_config = '''        if row is None:\n            row = RackNovaScanConfiguracion(\n                fecha_actualizacion=now,\n                actualizado_por=actor,\n            )\n'''
new_config = '''        if row is None:\n            # Una sola configuración lógica por empresa. Usar empresa_id como\n            # sync_uuid hace que Cloud y Local converjan aunque ambos creen su\n            # fila inicial antes de verse entre sí.\n            row = RackNovaScanConfiguracion(\n                fecha_actualizacion=now,\n                actualizado_por=actor,\n                sync_uuid=UUID(_rn_current_empresa_id(session)),\n            )\n'''
if old_config in text:
    text = text.replace(old_config, new_config, 1)
elif "sync_uuid=UUID(_rn_current_empresa_id(session))" not in text:
    raise SystemExit("scan config constructor anchor not found")

old_location = '''            actualizado_por=actor,\n        )\n        session.add(row)\n        rn_capture_sync_event(\n            session,\n            event_type="config.location.created",\n'''
new_location = '''            actualizado_por=actor,\n            # La etiqueta RNLOC se basa en id_ubicacion; reutilizamos la misma\n            # UUID como identidad B3 para conservar una identidad física única.\n            sync_uuid=location_id,\n        )\n        session.add(row)\n        rn_capture_sync_event(\n            session,\n            event_type="config.location.created",\n'''
if old_location in text:
    text = text.replace(old_location, new_location, 1)
elif "sync_uuid=location_id" not in text:
    raise SystemExit("location constructor anchor not found")
path.write_text(text, encoding="utf-8")

# main.py: normalize identities for tables that existed before sync support.
path = Path("main.py")
text = path.read_text(encoding="utf-8")
marker = "RACKNOVA_SCAN_SYNC_DETERMINISTIC_IDENTITY_20260903"
if marker not in text:
    anchor = '''                agregar_columna_si_falta(\n                    session, tabla_scan, "sync_origen_nodo",\n                    "VARCHAR(120) NULL",\n                )\n\n'''
    addition = '''                agregar_columna_si_falta(\n                    session, tabla_scan, "sync_origen_nodo",\n                    "VARCHAR(120) NULL",\n                )\n\n            # Identidad determinística para convergencia Cloud <-> Local.\n            # Configuración: una sola identidad por empresa.\n            session.exec(\n                text(\n                    "UPDATE racknova_scan_configuracion "\n                    "SET sync_uuid = empresa_id "\n                    "WHERE sync_uuid IS DISTINCT FROM empresa_id;"\n                )\n            )\n            # Ubicación: la identidad física RNLOC nace de id_ubicacion.\n            session.exec(\n                text(\n                    "UPDATE racknova_ubicacion_identidad "\n                    "SET sync_uuid = id_ubicacion "\n                    "WHERE sync_uuid IS DISTINCT FROM id_ubicacion;"\n                )\n            )\n            session.commit()\n            # RACKNOVA_SCAN_SYNC_DETERMINISTIC_IDENTITY_20260903\n\n'''
    if anchor not in text:
        raise SystemExit("main sync migration anchor not found")
    text = text.replace(anchor, addition, 1)
path.write_text(text, encoding="utf-8")
print("Deterministic Scan Sync identity patch applied")
