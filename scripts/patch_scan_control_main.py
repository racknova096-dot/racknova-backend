from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

import_line = "from scan_control_module import registrar_modulo_scan_control\n"
if import_line not in text:
    anchor = "from pos_module import registrar_modulo_pos\n"
    if anchor not in text:
        raise SystemExit("POS import anchor not found")
    text = text.replace(anchor, anchor + import_line, 1)

marker = "# RACKNOVA_SCAN_CONTROL_FASE3\n"
if marker not in text:
    anchor = "# ==========================================================\n# PUNTO DE VENTA — MÓDULO OPCIONAL\n# ==========================================================\n"
    if anchor not in text:
        raise SystemExit("POS registration anchor not found")
    block = (
        "# ==========================================================\n"
        "# SCAN CONTROL + IDENTIDAD DE UBICACIONES — OPCIONAL\n"
        "# ==========================================================\n"
        "# RACKNOVA_SCAN_CONTROL_FASE3\n"
        "registrar_modulo_scan_control(\n"
        "    app=app,\n"
        "    get_session=get_session,\n"
        "    require_roles=require_roles,\n"
        "    mexico_now=mexico_now,\n"
        ")\n\n"
    )
    text = text.replace(anchor, block + anchor, 1)

path.write_text(text, encoding="utf-8")
print("Patched main.py for Scan Control Fase 3")
