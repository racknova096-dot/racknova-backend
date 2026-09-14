from __future__ import annotations

from pathlib import Path
import ast


SYNC = Path("racknova_sync_worker.py")
POS = Path("pos_module.py")
NATIVE_CONFIG = Path("native/windows/racknova_native_config.py")
INSTALLER = Path("native/windows/configure_install.ps1")
ISS = Path("native/windows/RackNova.iss")
WORKFLOW = Path(".github/workflows/build-racknova-native-windows.yml")


def read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: no existe {path}")
    return path.read_text(encoding="utf-8")


def replace_function(text: str, function_name: str, callback) -> str:
    tree = ast.parse(text)
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(candidates) != 1:
        raise SystemExit(
            f"ERROR: esperaba 1 función {function_name}, encontré {len(candidates)}."
        )

    node = candidates[0]
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    lines[start:end] = [callback("".join(lines[start:end]))]
    result = "".join(lines)
    ast.parse(result)
    return result


# 1) Sync Cloud -> Local: qualify UPDATE RETURNING and keep inventory pull.
sync = read(SYNC)


def patch_update_json(segment: str) -> str:
    old = '    returning = ", ".join(_quote_ident(c) for c in meta["pk"]) or "sync_uuid"'
    new = (
        '    returning = ", ".join(\n'
        '        f"dst.{_quote_ident(c)}" for c in meta["pk"]\n'
        '    ) or "dst.sync_uuid"'
    )
    if old in segment:
        return segment.replace(old, new, 1)
    if 'f"dst.{_quote_ident(c)}"' in segment:
        return segment
    raise SystemExit("ERROR: no reconocí RETURNING en _update_json_record.")


sync = replace_function(sync, "_update_json_record", patch_update_json)

start = sync.find("PULL_ENTITY_PREFIXES = (")
end = sync.find("\n)", start)
if start < 0 or end < 0:
    raise SystemExit("ERROR: PULL_ENTITY_PREFIXES no encontrado.")
body = sync[start : end + 2]
if '"inventory.",' not in body:
    if '    "customer.",\n' not in body:
        raise SystemExit("ERROR: customer. no encontrado en PULL_ENTITY_PREFIXES.")
    body = body.replace(
        '    "customer.",\n',
        '    "customer.",\n    "inventory.",\n',
        1,
    )
    sync = sync[:start] + body + sync[end + 2 :]


def patch_pull(segment: str) -> str:
    if "OR entidad LIKE :p3" not in segment:
        old = "                 OR entidad LIKE :p2\n"
        if old not in segment:
            raise SystemExit("ERROR: condición :p2 no encontrada en pull_cloud_events.")
        segment = segment.replace(old, old + "                 OR entidad LIKE :p3\n", 1)
    if '"p3": prefixes[3] + "%"' not in segment:
        old = '"p2": prefixes[2] + "%",'
        if old not in segment:
            raise SystemExit("ERROR: parámetro p2 no encontrado en pull_cloud_events.")
        segment = segment.replace(old, old + '\n            "p3": prefixes[3] + "%",', 1)
    return segment


sync = replace_function(sync, "pull_cloud_events", patch_pull)
ast.parse(sync)


# 2) POS: Cloud configuration change enters the durable sync outbox.
pos = read(POS)
if "rn_capture_sync_event" not in pos:
    raise SystemExit("ERROR: pos_module.py no importa rn_capture_sync_event.")


def patch_pos_config(segment: str) -> str:
    marker = "RACKNOVA_NATIVE_UPDATE_POS_SYNC"
    if marker in segment:
        return segment
    lines = segment.splitlines(keepends=True)
    commits = [i for i, line in enumerate(lines) if line.strip() == "session.commit()"]
    if len(commits) != 1:
        raise SystemExit(
            "ERROR: actualizar_configuracion_pos debe tener un session.commit()."
        )
    index = commits[0]
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    block = (
        f'{indent}# RACKNOVA_NATIVE_UPDATE_POS_SYNC: config.pos.updated\n'
        f'{indent}rn_capture_sync_event(\n'
        f'{indent}    session,\n'
        f'{indent}    event_type="config.pos.updated",\n'
        f'{indent}    local_vars=locals(),\n'
        f'{indent})\n'
    )
    lines.insert(index, block)
    return "".join(lines)


pos = replace_function(pos, "actualizar_configuracion_pos", patch_pos_config)
ast.parse(pos)


# 3) Native Local always has POS capability; persisted pos_configuracion is the switch.
native_config = read(NATIVE_CONFIG)
if "RACKNOVA_NATIVE_POS_CAPABILITY" not in native_config:
    anchor = '    os.environ["RACKNOVA_MODE"] = "local"\n'
    if anchor not in native_config:
        raise SystemExit("ERROR: no encontré RACKNOVA_MODE=local.")
    native_config = native_config.replace(
        anchor,
        anchor
        + "\n    # RACKNOVA_NATIVE_POS_CAPABILITY\n"
        + '    os.environ["POS_ENABLED"] = "true"\n',
        1,
    )
ast.parse(native_config)


# 4) Installer keeps a Windows SCM timeout safety net.
installer = read(INSTALLER)
if "RACKNOVA_SERVICES_PIPE_TIMEOUT" not in installer:
    anchor = "& sc.exe config RackNovaLocal depend= RackNovaPostgreSQL16 | Out-Null"
    if anchor not in installer:
        raise SystemExit("ERROR: dependencia RackNovaLocal no encontrada.")
    block = r'''# RACKNOVA_SERVICES_PIPE_TIMEOUT
# Protección secundaria: el runtime se optimiza para iniciar más rápido,
# pero Windows conserva margen durante arranques lentos o recuperación de PG.
$RackNovaControlKey = "HKLM:\SYSTEM\CurrentControlSet\Control"

$RackNovaPipeTimeout = (
    Get-ItemProperty `
        -Path $RackNovaControlKey `
        -Name "ServicesPipeTimeout" `
        -ErrorAction SilentlyContinue
).ServicesPipeTimeout

if (
    (-not $RackNovaPipeTimeout) -or
    ([int64]$RackNovaPipeTimeout -lt 120000)
) {
    New-ItemProperty `
        -Path $RackNovaControlKey `
        -Name "ServicesPipeTimeout" `
        -PropertyType DWord `
        -Value 120000 `
        -Force | Out-Null

    Write-Log "ServicesPipeTimeout protegido a 120000 ms."
}

'''
    installer = installer.replace(anchor, block + anchor, 1)


# 5) PyInstaller service becomes onedir to avoid onefile extraction startup cost.
workflow = read(WORKFLOW)
lines = workflow.splitlines(keepends=True)
service_start = None
service_end = None
for i, line in enumerate(lines):
    if line.strip() == "- name: Build RackNovaLocalService.exe":
        service_start = i
        base_indent = len(line) - len(line.lstrip())
        break
if service_start is None:
    raise SystemExit("ERROR: Build RackNovaLocalService.exe no encontrado.")
for i in range(service_start + 1, len(lines)):
    stripped = lines[i].strip()
    indent = len(lines[i]) - len(lines[i].lstrip())
    if stripped.startswith("- name:") and indent == base_indent:
        service_end = i
        break
if service_end is None:
    service_end = len(lines)
service_block = "".join(lines[service_start:service_end])
if "--onefile" in service_block:
    service_block = service_block.replace("--onefile", "--onedir", 1)
elif "--onedir" not in service_block:
    anchor = "          pyinstaller `\n"
    if anchor not in service_block:
        raise SystemExit("ERROR: comando PyInstaller de servicio no reconocido.")
    service_block = service_block.replace(anchor, anchor + "            --onedir `\n", 1)
if "--contents-directory _service_internal" not in service_block:
    flag = "            --onedir `\n"
    if flag not in service_block:
        raise SystemExit("ERROR: --onedir no encontrado.")
    service_block = service_block.replace(
        flag,
        flag + "            --contents-directory _service_internal `\n",
        1,
    )
service_block = service_block.replace(
    "dist/RackNovaLocalService.exe",
    "dist/RackNovaLocalService/RackNovaLocalService.exe",
)
lines[service_start:service_end] = [service_block]
workflow = "".join(lines).replace(
    "dist/RackNovaLocalService.exe",
    "dist/RackNovaLocalService/RackNovaLocalService.exe",
)


# 6) Native build refuses to package a Dashboard without POS.
if "Validate Dashboard POS for Native" not in workflow:
    anchor = "      - name: Build Dashboard Local\n"
    if anchor not in workflow:
        raise SystemExit("ERROR: Build Dashboard Local no encontrado.")
    validation = r'''      - name: Validate Dashboard POS for Native
        working-directory: _dashboard
        shell: pwsh
        run: |
          $Required = @(
            "src/pages/PuntoVenta.tsx",
            "src/lib/pos.ts",
            "src/components/pos/POSFase3Panel.tsx",
            "src/components/layout/Navigation.tsx",
            "src/App.tsx"
          )

          foreach ($File in $Required) {
            if (-not (Test-Path $File)) {
              throw "Dashboard Native incompleto: falta $File"
            }
          }

          $Nav = Get-Content -LiteralPath "src/components/layout/Navigation.tsx" -Raw
          $Pos = Get-Content -LiteralPath "src/lib/pos.ts" -Raw
          $App = Get-Content -LiteralPath "src/App.tsx" -Raw

          if (-not $Nav.Contains("obtenerEstadoPOS")) {
            throw "Navigation no contiene obtenerEstadoPOS."
          }
          if (-not $Nav.Contains("posState?.habilitado")) {
            throw "Navigation no valida estado habilitado del POS."
          }
          if (-not $Pos.Contains("/pos/estado")) {
            throw "src/lib/pos.ts no contiene /pos/estado."
          }
          if (-not $Pos.Contains("/pos/configuracion")) {
            throw "src/lib/pos.ts no contiene /pos/configuracion."
          }
          if (-not $App.Contains("PuntoVenta")) {
            throw "App.tsx no contiene PuntoVenta."
          }

          Write-Host "POS Dashboard validado."
          Write-Host "Dashboard commit:"
          git rev-parse HEAD

'''
    workflow = workflow.replace(anchor, validation + anchor, 1)


# Hotfix artifact must carry the onedir runtime too.
workflow_lines = workflow.splitlines()
for index, line in enumerate(workflow_lines):
    if "Hotfix-Binaries" not in line:
        continue
    step_start = index
    while step_start >= 0 and not workflow_lines[step_start].strip().startswith("- name:"):
        step_start -= 1
    if step_start < 0:
        continue
    step_indent = len(workflow_lines[step_start]) - len(workflow_lines[step_start].lstrip())
    step_end = index + 1
    while step_end < len(workflow_lines):
        candidate = workflow_lines[step_end]
        candidate_indent = len(candidate) - len(candidate.lstrip())
        if candidate.strip().startswith("- name:") and candidate_indent == step_indent:
            break
        step_end += 1
    segment = workflow_lines[step_start:step_end]
    if any("_service_internal" in row for row in segment):
        break
    path_index = None
    for relative_index, row in enumerate(segment):
        if row.strip() == "path: |":
            path_index = step_start + relative_index
            break
    if path_index is None:
        continue
    path_indent = workflow_lines[path_index][: len(workflow_lines[path_index]) - len(workflow_lines[path_index].lstrip())] + "  "
    workflow_lines.insert(
        path_index + 1,
        path_indent + "dist/RackNovaLocalService/_service_internal",
    )
    break
workflow = "\n".join(workflow_lines) + "\n"


# 7) Inno copies the onedir internals while keeping the installed EXE path stable.
iss = read(ISS)
old_service_source = (
    r'Source: "..\..\dist\RackNovaLocalService.exe"; '
    r'DestDir: "{app}"; Flags: ignoreversion'
)
new_service_source = (
    r'Source: "..\..\dist\RackNovaLocalService\RackNovaLocalService.exe"; '
    r'DestDir: "{app}"; Flags: ignoreversion'
)
internal_source = (
    r'Source: "..\..\dist\RackNovaLocalService\_service_internal\*"; '
    r'DestDir: "{app}\_service_internal"; '
    r'Flags: ignoreversion recursesubdirs createallsubdirs'
)
if old_service_source in iss:
    iss = iss.replace(old_service_source, new_service_source + "\n" + internal_source, 1)
elif new_service_source in iss:
    if internal_source not in iss:
        iss = iss.replace(new_service_source, new_service_source + "\n" + internal_source, 1)
else:
    raise SystemExit("ERROR: Source RackNovaLocalService no reconocido en RackNova.iss.")


# Guardrails.
for name, source in (
    (SYNC, sync),
    (POS, pos),
    (NATIVE_CONFIG, native_config),
):
    ast.parse(source)

for token in (
    'f"dst.{_quote_ident(c)}"',
    '"inventory.",',
    "OR entidad LIKE :p3",
    '"p3": prefixes[3] + "%"',
):
    if token not in sync:
        raise SystemExit(f"ERROR: Sync incompleto; falta {token}")

for token in (
    "RACKNOVA_NATIVE_UPDATE_POS_SYNC",
    'event_type="config.pos.updated"',
):
    if token not in pos:
        raise SystemExit(f"ERROR: POS incompleto; falta {token}")

for token in (
    "RACKNOVA_NATIVE_POS_CAPABILITY",
    'os.environ["POS_ENABLED"] = "true"',
):
    if token not in native_config:
        raise SystemExit(f"ERROR: Native POS incompleto; falta {token}")

for token in (
    "--onedir",
    "--contents-directory _service_internal",
    "Validate Dashboard POS for Native",
    "dist/RackNovaLocalService/RackNovaLocalService.exe",
):
    if token not in workflow:
        raise SystemExit(f"ERROR: workflow incompleto; falta {token}")

if "_service_internal" not in iss:
    raise SystemExit("ERROR: Inno no incluye _service_internal.")


SYNC.write_text(sync, encoding="utf-8")
POS.write_text(pos, encoding="utf-8")
NATIVE_CONFIG.write_text(native_config, encoding="utf-8")
INSTALLER.write_text(installer, encoding="utf-8")
WORKFLOW.write_text(workflow, encoding="utf-8")
ISS.write_text(iss, encoding="utf-8")

print("RackNova Native stability update aplicada y validada estáticamente.")
