from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"Anchor not found: {label}")
    return text.replace(old, new, 1)

# 1) Scan config must no longer be captured by B3.
path = Path("racknova_sync_capture.py")
text = path.read_text(encoding="utf-8")
text = text.replace('    "racknova_scan_",\n', '', 1)
text = replace_once(
    text,
    '    "racknova_sync_estado",\n}',
    '    "racknova_sync_estado",\n    "racknova_scan_configuracion",\n}',
    "capture excluded tables",
)
path.write_text(text, encoding="utf-8")

# 2) Worker must ignore scan config records while keeping RNLOC sync.
# Explicit exclusion is necessary because the generic `rack` prefix would
# otherwise classify racknova_scan_configuracion as commercial.
path = Path("racknova_sync_worker.py")
text = path.read_text(encoding="utf-8")
text = text.replace('    "racknova_scan_",\n', '', 1)
text = text.replace("                      OR t.table_name LIKE 'racknova_scan_%'\n", '', 1)
text = replace_once(
    text,
    '    "racknova_sync_id_map",\n}',
    '    "racknova_sync_id_map",\n    "racknova_scan_configuracion",\n}',
    "worker excluded tables",
)
path.write_text(text, encoding="utf-8")

# 3) The legacy backend endpoint remains compatible per backend node,
# but it no longer emits a sync event. Location events remain synced.
path = Path("scan_control_module.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    'from multiempresa_tenant import current_empresa_id as _rn_current_empresa_id\n',
    '',
    1,
)
text = text.replace(
    '# Fase 3: capacidades opcionales, controladas por la empresa.\n',
    '# Fase 3: preferencias de escaneo locales por terminal; ubicaciones compartidas.\n',
    1,
)
old = '''        if row is None:\n            # Una sola configuración lógica por empresa. Usar empresa_id como\n            # sync_uuid hace que Cloud y Local converjan aunque ambos creen su\n            # fila inicial antes de verse entre sí.\n            row = RackNovaScanConfiguracion(\n                fecha_actualizacion=now,\n                actualizado_por=actor,\n                sync_uuid=UUID(_rn_current_empresa_id(session)),\n            )\n'''
new = '''        if row is None:\n            # Compatibilidad con dashboards anteriores: esta fila pertenece\n            # únicamente a este backend/nodo y nunca se replica por RackNova Sync.\n            row = RackNovaScanConfiguracion(\n                fecha_actualizacion=now,\n                actualizado_por=actor,\n            )\n'''
text = replace_once(text, old, new, "scan config constructor")
old = '''        session.add(row)\n        rn_capture_sync_event(\n            session,\n            event_type="config.scan.updated",\n            local_vars=locals(),\n        )\n        session.commit()\n'''
new = '''        session.add(row)\n        # Preferencia deliberadamente local: no genera outbox ni viaja a Cloud.\n        session.commit()\n'''
text = replace_once(text, old, new, "scan config sync event")
path.write_text(text, encoding="utf-8")

print("Terminal-local scan preference backend patch applied")
