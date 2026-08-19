#!/usr/bin/env python3
# ============================================================
# RackNova Local — Fase 2.6
# PostgreSQL Local + bootstrap empresa + nodo + FastAPI LAN
# ============================================================
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

# Dependencias del backend se cargan de forma diferida. Así el provisionador
# puede instalar requirements.txt incluso en una PC nueva.
create_engine = None
sa_text = None
Session = None


ROOT = Path.cwd()
STATE_DIR = ROOT / ".racknova_local"
CONFIG_FILE = STATE_DIR / "config.json"
COMPOSE_FILE = STATE_DIR / "docker-compose.racknova-local.yml"
BOOT_LOG = STATE_DIR / "bootstrap_backend.log"
SQL_001 = Path(__file__).with_name("001_multiempresa_fase1.sql")
SQL_002 = Path(__file__).with_name("002_multiempresa_fase2_local_first.sql")

DEFAULT_EMPRESA = "11111111-1111-4111-8111-111111111111"
DEFAULT_DB_PORT = 54329
DEFAULT_API_PORT = 8000
BOOT_PORT = 18765
BOOTSTRAP_VERSION = 1

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INFRA_TABLES = {
    "empresas",
    "empresa_usuarios",
    "usuario",
    "racknova_platform_admins",
    "racknova_nodos",
    "racknova_sync_outbox",
    "racknova_sync_estado",
    "racknova_sync_inbox",
    "racknova_sync_cursor",
    "racknova_sync_id_map",
}


def fail(message: str) -> None:
    raise SystemExit(f"❌ {message}")


def info(message: str) -> None:
    print(f"ℹ️ {message}")


def ok(message: str) -> None:
    print(f"✅ {message}")


def run(
    args: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        input=input_text,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if check and proc.returncode != 0:
        output = proc.stdout or ""
        fail(
            "Falló comando:\n"
            + " ".join(args)
            + ("\n\n" + output[-5000:] if output else "")
        )
    return proc


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def ensure_python_dependencies() -> None:
    global create_engine, sa_text, Session
    try:
        from sqlalchemy import create_engine as _create_engine, text as _sa_text
        from sqlmodel import Session as _Session
        import uvicorn  # noqa: F401
    except Exception:
        requirements = ROOT / "requirements.txt"
        if not requirements.exists():
            fail(
                "Faltan dependencias Python (sqlalchemy/sqlmodel/uvicorn) y "
                "no existe requirements.txt en este backend."
            )
        info("Instalando dependencias Python del backend...")
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(requirements),
            ],
            capture=False,
        )
        try:
            from sqlalchemy import create_engine as _create_engine, text as _sa_text
            from sqlmodel import Session as _Session
            import uvicorn  # noqa: F401
        except Exception as exc:
            fail(f"Las dependencias Python no quedaron disponibles: {exc}")

    create_engine = _create_engine
    sa_text = _sa_text
    Session = _Session


def docker_compose_base() -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE)]


def sanitize_node_code(value: str) -> str:
    value = str(value or "").strip().upper()
    value = re.sub(r"[^A-Z0-9._-]+", "-", value)
    return value.strip("-._")[:120]


def validate_uuid(value: str, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except Exception:
        fail(f"{label} no es UUID válido: {value!r}")


def json_request(
    *,
    method: str,
    url: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 120,
) -> Any:
    data = None
    headers = {
        "Accept": "application/json",
        "X-RackNova-Sync-Secret": secret,
        "User-Agent": "RackNova-Local-Provision/2.6",
    }
    if payload is not None:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=url,
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"Cloud respondió HTTP {exc.code}: {raw[:3000]}")
    except Exception as exc:
        fail(f"No se pudo conectar con RackNova Cloud: {exc}")


def local_ips() -> list[str]:
    values: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            values.append(ip)
    except Exception:
        pass

    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(item[4][0])
            if ip and not ip.startswith("127.") and ip not in values:
                values.append(ip)
    except Exception:
        pass
    return values


def write_git_local_exclude() -> None:
    exclude = ROOT / ".git" / "info" / "exclude"
    if not exclude.parent.exists():
        return
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    marker = ".racknova_local/"
    if marker not in text.splitlines():
        exclude.write_text(text.rstrip() + "\n" + marker + "\n", encoding="utf-8")


def compose_text(*, password: str, db_port: int, container_name: str) -> str:
    # El puerto PostgreSQL se publica SOLO en loopback: terminales LAN nunca
    # se conectan directamente a PostgreSQL, sino al FastAPI local.
    return f"""services:
  postgres:
    image: postgres:16-alpine
    container_name: {container_name}
    restart: unless-stopped
    environment:
      POSTGRES_DB: racknova
      POSTGRES_USER: racknova
      POSTGRES_PASSWORD: {password}
    ports:
      - "127.0.0.1:{db_port}:5432"
    volumes:
      - racknova_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U racknova -d racknova"]
      interval: 3s
      timeout: 3s
      retries: 30

volumes:
  racknova_pgdata:
"""


def wait_postgres(timeout: int = 90) -> None:
    deadline = time.time() + timeout
    base = docker_compose_base()
    while time.time() < deadline:
        proc = run(
            base + ["exec", "-T", "postgres", "pg_isready", "-U", "racknova", "-d", "racknova"],
            check=False,
        )
        if proc.returncode == 0:
            ok("PostgreSQL local está listo.")
            return
        time.sleep(2)
    fail("PostgreSQL local no quedó listo dentro del tiempo esperado.")


def psql(sql: str) -> None:
    proc = run(
        docker_compose_base()
        + [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "racknova",
            "-d",
            "racknova",
        ],
        input_text=sql,
        check=False,
    )
    if proc.returncode != 0:
        fail("SQL local falló:\n" + (proc.stdout or "")[-6000:])


def backend_env(config: dict[str, Any], *, autostart: bool) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": config["database_url"],
            "RACKNOVA_MODE": "local",
            "RACKNOVA_EMPRESA_ID": config["empresa_id"],
            "RACKNOVA_NODE_CODE": config["node_code"],
            "RACKNOVA_NODE_NAME": config["node_name"],
            "RACKNOVA_NODE_TYPE": "LOCAL_SERVER",
            "RACKNOVA_CLOUD_URL": config["cloud_url"],
            "RACKNOVA_SYNC_SECRET": config["sync_secret"],
            "RACKNOVA_SYNC_AUTOSTART": "true" if autostart else "false",
            "RACKNOVA_SYNC_INTERVAL_SECONDS": str(config.get("sync_interval", 15)),
        }
    )
    return env


def wait_http(port: int, timeout: int = 75) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/openapi.json"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def initialize_backend_schema(config: dict[str, Any]) -> None:
    """
    Arranca exactamente el mismo backend RackNova contra PostgreSQL Local.

    Esto deja que la inicialización existente cree las tablas SQLModel y las
    extensiones POS que hoy forman parte del startup normal.
    """
    BOOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with BOOT_LOG.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(BOOT_PORT),
            ],
            cwd=ROOT,
            env=backend_env(config, autostart=False),
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            if not wait_http(BOOT_PORT):
                if proc.poll() is not None:
                    tail = BOOT_LOG.read_text(encoding="utf-8", errors="replace")[-8000:]
                    fail("El backend local no pudo iniciar:\n" + tail)
                fail(
                    "El backend local no respondió durante bootstrap. "
                    f"Revisa {BOOT_LOG}."
                )
            ok("FastAPI inició correctamente contra PostgreSQL Local.")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def quote_ident(name: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(str(name or "")):
        fail(f"Identificador SQL no permitido en bootstrap: {name!r}")
    return f'"{name}"'


def table_exists(session: Session, table: str) -> bool:
    value = session.connection().execute(
        sa_text("SELECT to_regclass(:name)"),
        {"name": f"public.{table}"},
    ).scalar_one_or_none()
    return bool(value)


def table_columns(session: Session, table: str) -> set[str]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:table
            """
        ),
        {"table": table},
    ).scalars().all()
    return {str(row) for row in rows}


def tenant_tables(session: Session) -> list[str]:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT DISTINCT t.table_name
            FROM information_schema.tables t
            JOIN information_schema.columns c
              ON c.table_schema=t.table_schema
             AND c.table_name=t.table_name
            WHERE t.table_schema='public'
              AND t.table_type='BASE TABLE'
              AND c.column_name='empresa_id'
            ORDER BY t.table_name
            """
        )
    ).scalars().all()
    return [
        str(row)
        for row in rows
        if str(row) not in INFRA_TABLES and SAFE_IDENTIFIER.fullmatch(str(row))
    ]



def prepare_missing_schema(
    session: Session,
    *,
    tables: list[dict[str, Any]],
) -> list[str]:
    """
    Crea solo tablas que falten en la DB local usando el DDL exportado por
    RackNova Cloud. Las tablas SQLModel normales ya deberían existir; esto
    cubre tablas lazy creadas por módulos POS/configuración.
    """
    missing = [
        item
        for item in tables
        if SAFE_IDENTIFIER.fullmatch(str(item.get("table") or ""))
        and not table_exists(session, str(item.get("table")))
    ]
    if not missing:
        return []

    created: list[str] = []

    # 1) secuencias y tablas sin constraints FK.
    for item in missing:
        table = str(item["table"])
        schema = dict(item.get("schema") or {})
        create_sql = str(schema.get("create_sql") or "").strip()
        if not create_sql:
            fail(f"Cloud no envió DDL para la tabla faltante {table}.")

        for statement in list(schema.get("pre_sql") or []):
            session.connection().execute(sa_text(str(statement)))
        session.connection().execute(sa_text(create_sql))
        created.append(table)

    # 2) constraints. FKs se intentan por rondas porque algunas tablas lazy
    # pueden depender de otras creadas en este mismo bootstrap.
    pending: list[tuple[str, str]] = []
    for item in missing:
        table = str(item["table"])
        schema = dict(item.get("schema") or {})
        for statement in list(schema.get("constraints_sql") or []):
            pending.append((table, str(statement)))

    last_errors: dict[str, str] = {}
    for _round in range(max(2, len(pending) + 1)):
        if not pending:
            break
        progress = False
        next_pending: list[tuple[str, str]] = []
        for table, statement in pending:
            try:
                with session.begin_nested():
                    session.connection().execute(sa_text(statement))
                progress = True
            except Exception as exc:
                last_errors[table] = str(exc)
                next_pending.append((table, statement))
        pending = next_pending
        if not progress:
            break

    if pending:
        details: dict[str, str] = {}
        for table, _statement in pending:
            details[table] = last_errors.get(table, "constraint pendiente")
        session.rollback()
        fail(
            "No pude reconstruir constraints de algunas tablas lazy:\\n"
            + json.dumps(details, ensure_ascii=False, indent=2)
        )

    # 3) índices no respaldados por constraints.
    for item in missing:
        schema = dict(item.get("schema") or {})
        for statement in list(schema.get("indexes_sql") or []):
            try:
                session.connection().execute(sa_text(str(statement)))
            except Exception as exc:
                session.rollback()
                fail(
                    f"No pude crear un índice de {item.get('table')}: {exc}"
                )

    session.commit()
    return created


def assert_fresh_local(session: Session) -> None:
    occupied: list[tuple[str, int]] = []
    for table in tenant_tables(session):
        q = quote_ident(table)
        try:
            count = int(
                session.connection().execute(
                    sa_text(f"SELECT COUNT(*) FROM {q}")
                ).scalar_one()
                or 0
            )
        except Exception:
            continue
        if count:
            occupied.append((table, count))

    if occupied:
        preview = ", ".join(f"{name}={count}" for name, count in occupied[:12])
        fail(
            "La base PostgreSQL local ya contiene información comercial. "
            "El bootstrap inicial no sobrescribe una instalación existente. "
            f"Tablas con datos: {preview}"
        )


def insert_json_row(
    session: Session,
    *,
    table: str,
    data: dict[str, Any],
) -> int:
    q = quote_ident(table)
    available = table_columns(session, table)
    cols = [key for key in data.keys() if key in available and SAFE_IDENTIFIER.fullmatch(key)]
    if not cols:
        return 0

    columns_sql = ", ".join(quote_ident(col) for col in cols)
    source_sql = ", ".join(f"src.{quote_ident(col)}" for col in cols)

    sql = (
        f"INSERT INTO {q} ({columns_sql}) "
        f"SELECT {source_sql} "
        f"FROM jsonb_populate_record(NULL::{q}, CAST(:data AS JSONB)) AS src "
        "ON CONFLICT DO NOTHING"
    )
    result = session.connection().execute(
        sa_text(sql),
        {
            "data": json.dumps(
                data,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
        },
    )
    return int(result.rowcount or 0)


def import_table_rows_with_fk_passes(
    session: Session,
    *,
    tables: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    pending: list[tuple[str, dict[str, Any]]] = []
    missing_schema: list[dict[str, Any]] = []

    for item in tables:
        table = str(item.get("table") or "")
        rows = list(item.get("rows") or [])
        if not rows:
            continue
        if not SAFE_IDENTIFIER.fullmatch(table) or not table_exists(session, table):
            missing_schema.append(
                {
                    "table": table,
                    "rows": len(rows),
                    "reason": "table_missing",
                }
            )
            continue

        local_cols = table_columns(session, table)
        cloud_cols = {str(c) for c in list(item.get("columns") or [])}
        missing_cols = sorted(cloud_cols - local_cols)
        if missing_cols:
            missing_schema.append(
                {
                    "table": table,
                    "rows": len(rows),
                    "reason": "columns_missing",
                    "columns": missing_cols,
                }
            )
            continue

        for row in rows:
            pending.append((table, dict(row)))

    if missing_schema:
        return 0, missing_schema

    inserted = 0
    last_errors: dict[str, str] = {}

    # Varias rondas permiten que padres entren antes que hijos sin tener que
    # hardcodear el grafo de todas las tablas POS actuales y futuras.
    for _round in range(max(3, len({table for table, _ in pending}) + 3)):
        if not pending:
            break
        progress = False
        next_pending: list[tuple[str, dict[str, Any]]] = []

        for table, row in pending:
            try:
                with session.begin_nested():
                    inserted += insert_json_row(session, table=table, data=row)
                progress = True
            except Exception as exc:
                last_errors[table] = str(exc)
                next_pending.append((table, row))

        pending = next_pending
        if not progress:
            break

    if pending:
        unresolved: dict[str, int] = {}
        for table, _row in pending:
            unresolved[table] = unresolved.get(table, 0) + 1
        details = [
            {
                "table": table,
                "rows": count,
                "reason": "fk_or_constraint",
                "error": last_errors.get(table, "")[:500],
            }
            for table, count in sorted(unresolved.items())
        ]
        return inserted, details

    return inserted, []


def reset_sequences(session: Session) -> int:
    rows = session.connection().execute(
        sa_text(
            """
            SELECT
                c.table_name,
                c.column_name,
                pg_get_serial_sequence(
                    format('%I.%I', c.table_schema, c.table_name),
                    c.column_name
                ) AS seq
            FROM information_schema.columns c
            WHERE c.table_schema='public'
            ORDER BY c.table_name, c.ordinal_position
            """
        )
    ).mappings().all()

    changed = 0
    for row in rows:
        seq = row.get("seq")
        table = str(row["table_name"])
        column = str(row["column_name"])
        if not seq or not SAFE_IDENTIFIER.fullmatch(table) or not SAFE_IDENTIFIER.fullmatch(column):
            continue

        qtable = quote_ident(table)
        qcol = quote_ident(column)
        maximum = session.connection().execute(
            sa_text(f"SELECT MAX({qcol}) FROM {qtable}")
        ).scalar_one_or_none()
        if maximum is None:
            continue

        session.connection().execute(
            sa_text("SELECT setval(to_regclass(:seq), :value, true)"),
            {"seq": str(seq), "value": int(maximum)},
        )
        changed += 1
    return changed


def set_cloud_cursor(
    session: Session,
    *,
    empresa_id: str,
    node_code: str,
    cursor: dict[str, Any],
) -> None:
    created = cursor.get("last_created_at")
    event_id = cursor.get("last_event_id")
    if not created and not event_id:
        return

    session.connection().execute(
        sa_text(
            """
            INSERT INTO racknova_sync_cursor (
                empresa_id, node_code, direccion,
                last_created_at, last_event_id, actualizado_en
            )
            VALUES (
                CAST(:empresa AS UUID), :node, 'CLOUD_TO_LOCAL',
                CAST(:created AS TIMESTAMPTZ), CAST(:event AS UUID), NOW()
            )
            ON CONFLICT (empresa_id, node_code, direccion)
            DO UPDATE SET
                last_created_at=EXCLUDED.last_created_at,
                last_event_id=EXCLUDED.last_event_id,
                actualizado_en=NOW()
            """
        ),
        {
            "empresa": empresa_id,
            "node": node_code,
            "created": str(created) if created else None,
            "event": str(event_id) if event_id else None,
        },
    )


def bootstrap_local_data(config: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    if int(package.get("bootstrap_version") or 0) != BOOTSTRAP_VERSION:
        fail(
            "Versión de bootstrap Cloud incompatible: "
            f"{package.get('bootstrap_version')!r}"
        )
    if str(package.get("empresa_id")) != config["empresa_id"]:
        fail("El paquete Cloud pertenece a otra empresa.")

    engine = create_engine(config["database_url"], pool_pre_ping=True)
    with Session(engine) as session:
        created_schema = prepare_missing_schema(
            session,
            tables=list(package.get("tables") or []),
        )
        if created_schema:
            ok(
                "Tablas lazy reconstruidas desde Cloud: "
                + ", ".join(created_schema)
            )

        assert_fresh_local(session)
        session.connection().execute(
            sa_text("SELECT set_config('app.racknova_sync_apply', '1', true)")
        )

        # Elimina únicamente los residuos de la inicialización de una base
        # NUEVA (admin local de respaldo/membresía legacy).
        session.connection().execute(sa_text("DELETE FROM empresa_usuarios"))
        try:
            session.connection().execute(sa_text("DELETE FROM racknova_platform_admins"))
        except Exception:
            pass
        try:
            session.connection().execute(sa_text("DELETE FROM usuario"))
        except Exception as exc:
            session.rollback()
            fail(
                "No pude limpiar el usuario temporal del bootstrap. "
                f"La base no parece fresca: {exc}"
            )

        company = dict(package.get("company") or {})
        if not company:
            fail("El paquete Cloud no contiene la empresa.")

        # Una instalación RackNova Local queda ligada a UNA empresa.
        # En la base nueva no debe sobrevivir la empresa principal temporal
        # creada por las migraciones si estamos provisionando otra empresa.
        session.connection().execute(
            sa_text(
                """
                DELETE FROM empresas
                WHERE id_empresa <> CAST(:empresa AS UUID)
                """
            ),
            {"empresa": config["empresa_id"]},
        )
        session.connection().execute(
            sa_text(
                """
                DELETE FROM empresas
                WHERE id_empresa = CAST(:empresa AS UUID)
                """
            ),
            {"empresa": config["empresa_id"]},
        )
        insert_json_row(session, table="empresas", data=company)

        for user in list(package.get("users") or []):
            insert_json_row(session, table="usuario", data=dict(user))

        for membership in list(package.get("memberships") or []):
            insert_json_row(session, table="empresa_usuarios", data=dict(membership))

        inserted, unresolved = import_table_rows_with_fk_passes(
            session,
            tables=list(package.get("tables") or []),
        )
        if unresolved:
            session.rollback()
            fail(
                "El esquema local todavía no coincide con Cloud o quedaron "
                "dependencias sin resolver.\n"
                + json.dumps(unresolved[:20], ensure_ascii=False, indent=2)
            )

        sequence_count = reset_sequences(session)
        session.commit()

    # B3 crea inbox/cursor/id_map/triggers y el nodo local usando el MISMO
    # código del backend, ya contra PostgreSQL Local.
    from racknova_runtime import ensure_node_registered, load_runtime_config
    from racknova_sync_worker import ensure_sync_schema

    with Session(engine) as session:
        key = "racknova_empresa_id"
        try:
            import multiempresa_tenant as rn_tenant
            key = getattr(rn_tenant, "SESSION_EMPRESA_KEY", key)
        except Exception:
            pass
        session.info[key] = config["empresa_id"]
        session.connection().execute(
            sa_text("SELECT set_config('app.racknova_empresa_id', :empresa, true)"),
            {"empresa": config["empresa_id"]},
        )

        ensure_sync_schema(session)
        node = ensure_node_registered(
            session=session,
            empresa_id=config["empresa_id"],
            config=load_runtime_config(),
        )

    # ensure_node_registered hace commit y cierra esa transacción. Abrimos una
    # sesión nueva para fijar el cursor del snapshot inicial.
    with Session(engine) as session:
        set_cloud_cursor(
            session,
            empresa_id=config["empresa_id"],
            node_code=config["node_code"],
            cursor=dict(package.get("cloud_cursor") or {}),
        )
        session.commit()

    return {
        "commercial_rows_imported": inserted,
        "sequences_reset": sequence_count,
        "node": node,
    }


def register_node_in_cloud(config: dict[str, Any]) -> dict[str, Any]:
    url = config["cloud_url"].rstrip("/") + "/sync/v1/nodes/register"
    return json_request(
        method="POST",
        url=url,
        secret=config["sync_secret"],
        payload={
            "empresa_id": config["empresa_id"],
            "node_code": config["node_code"],
            "node_name": config["node_name"],
            "node_type": "LOCAL_SERVER",
            "app_version": config.get("app_version") or "local-2.6",
        },
        timeout=30,
    )


def fetch_bootstrap(config: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "empresa_id": config["empresa_id"],
            "max_rows_per_table": 100000,
        }
    )
    url = config["cloud_url"].rstrip("/") + "/sync/v1/bootstrap/export?" + query
    info("Descargando snapshot inicial de la empresa desde Cloud...")
    result = json_request(
        method="GET",
        url=url,
        secret=config["sync_secret"],
        timeout=180,
    )
    ok(
        "Snapshot Cloud recibido: "
        f"{result.get('total_commercial_rows', 0)} filas comerciales."
    )
    return result


def config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    cloud_url = str(args.cloud_url or "").strip()
    if not cloud_url:
        cloud_url = input("URL RackNova Cloud (Render): ").strip()
    cloud_url = cloud_url.rstrip("/")
    if not cloud_url.startswith(("https://", "http://")):
        fail("RACKNOVA_CLOUD_URL debe comenzar con https:// o http://")

    empresa_id = str(args.empresa_id or DEFAULT_EMPRESA).strip()
    empresa_id = validate_uuid(empresa_id, "empresa_id")

    secret = str(args.sync_secret or "").strip()
    if not secret:
        secret = getpass.getpass("RACKNOVA_SYNC_SECRET (no se mostrará): ").strip()
    if len(secret) < 20:
        fail("RACKNOVA_SYNC_SECRET parece demasiado corto.")

    default_code = sanitize_node_code(f"LOCAL-{socket.gethostname()}") or "LOCAL-001"
    node_code = sanitize_node_code(args.node_code or default_code)
    if not node_code:
        fail("node_code inválido.")

    node_name = str(
        args.node_name
        or f"RackNova Local - {socket.gethostname()}"
    ).strip()[:180]

    password = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    db_port = int(args.db_port or DEFAULT_DB_PORT)

    return {
        "fase": "2.6",
        "empresa_id": empresa_id,
        "cloud_url": cloud_url,
        "sync_secret": secret,
        "node_code": node_code,
        "node_name": node_name,
        "database_password": password,
        "database_port": db_port,
        "database_url": (
            f"postgresql://racknova:{password}@127.0.0.1:{db_port}/racknova"
        ),
        "api_port": int(args.api_port or DEFAULT_API_PORT),
        "sync_interval": int(args.sync_interval or 15),
        "app_version": "local-2.6",
    }


def write_launchers() -> None:
    (ROOT / "iniciar_racknova_local.bat").write_text(
        "@echo off\r\n"
        "cd /d %~dp0\r\n"
        "python racknova_local_provision.py serve\r\n",
        encoding="utf-8",
    )
    (ROOT / "diagnostico_racknova_local.bat").write_text(
        "@echo off\r\n"
        "cd /d %~dp0\r\n"
        "python racknova_local_provision.py diagnose\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    (ROOT / "habilitar_firewall_racknova.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$rule='RackNova Local API 8000'\n"
        "if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {\n"
        "  New-NetFirewallRule -DisplayName $rule -Direction Inbound "
        "-Action Allow -Protocol TCP -LocalPort 8000 | Out-Null\n"
        "}\n"
        "Write-Host 'RackNova: regla TCP 8000 lista.'\n",
        encoding="utf-8",
    )


def save_config(config: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        fail(
            f"No existe {CONFIG_FILE}. Ejecuta primero "
            "`python racknova_local_provision.py provision`."
        )
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def provision(args: argparse.Namespace) -> None:
    print("==============================================================")
    print(" RackNova Fase 2.6 — PostgreSQL Local + Nodo + LAN")
    print("==============================================================")

    ensure_python_dependencies()

    required = [
        ROOT / "main.py",
        ROOT / "racknova_sync_worker.py",
        ROOT / "racknova_local_first.py",
        SQL_001,
        SQL_002,
    ]
    for path in required:
        if not path.exists():
            fail(f"Falta {path.name}. Usa el paquete Fase 2.6 completo.")

    if not command_exists("docker"):
        fail(
            "No encontré Docker. En Windows instala/inicia Docker Desktop "
            "antes de crear RackNova Local."
        )

    proc = run(["docker", "compose", "version"], check=False)
    if proc.returncode != 0:
        fail("Docker Compose v2 no está disponible.")

    config = config_from_args(args)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_git_local_exclude()

    if CONFIG_FILE.exists():
        fail(
            "Ya existe configuración RackNova Local. "
            "No voy a reprovisionar ni borrar una instalación existente "
            "automáticamente. Usa `diagnose` para revisar su estado."
        )

    container_name = (
        "racknova-postgres-"
        + re.sub(r"[^a-z0-9]+", "-", config["node_code"].lower()).strip("-")
    )[:63]

    COMPOSE_FILE.write_text(
        compose_text(
            password=config["database_password"],
            db_port=config["database_port"],
            container_name=container_name,
        ),
        encoding="utf-8",
    )
    save_config(config)

    info("Creando PostgreSQL 16 local con volumen persistente...")
    run(docker_compose_base() + ["up", "-d"])
    wait_postgres()

    # Primera pasada: crea empresas/membresías antes de levantar el backend.
    info("Aplicando base multiempresa inicial...")
    psql(SQL_001.read_text(encoding="utf-8"))

    info("Inicializando el esquema del backend actual...")
    initialize_backend_schema(config)

    # Segunda pasada: ahora 001 tenantiza todas las tablas que el backend creó.
    psql(SQL_001.read_text(encoding="utf-8"))
    info("Aplicando migración Local-First Fase 2...")
    psql(SQL_002.read_text(encoding="utf-8"))

    # Confirmar que el backend sigue arrancando con el esquema final.
    initialize_backend_schema(config)

    # A partir de aquí las importaciones de racknova_runtime/B3 deben ver
    # exactamente el mismo runtime local que verá Uvicorn.
    os.environ.update(backend_env(config, autostart=False))

    package = fetch_bootstrap(config)
    imported = bootstrap_local_data(config, package)
    ok(
        "Datos iniciales cargados: "
        f"{imported['commercial_rows_imported']} filas comerciales."
    )

    remote_node = register_node_in_cloud(config)
    ok(
        "Nodo local registrado en Cloud: "
        f"{remote_node.get('node', {}).get('codigo', config['node_code'])}"
    )

    write_launchers()

    ips = local_ips()
    print()
    print("✅ RACKNOVA LOCAL FASE 2.6 PROVISIONADO")
    print()
    print(f"Empresa: {config['empresa_id']}")
    print(f"Nodo:    {config['node_code']}")
    print(f"DB:      PostgreSQL 16 @ 127.0.0.1:{config['database_port']}")
    print(f"API:     0.0.0.0:{config['api_port']}")
    if ips:
        print("LAN:")
        for ip in ips:
            print(f"  http://{ip}:{config['api_port']}")
            print(f"  http://{ip}:{config['api_port']}/docs")
    else:
        print("LAN: no pude detectar una IPv4 no-loopback automáticamente.")
    print()
    print("Para iniciar RackNova Local:")
    print("  python racknova_local_provision.py serve")
    if os.name == "nt":
        print("  o doble clic en iniciar_racknova_local.bat")
        print()
        print("Para otras PCs de la LAN, abre TCP 8000 una sola vez como Administrador:")
        print("  powershell -ExecutionPolicy Bypass -File habilitar_firewall_racknova.ps1")
    print()
    print("El puerto PostgreSQL NO está expuesto a la LAN; solo FastAPI.")


def serve(_args: argparse.Namespace) -> None:
    ensure_python_dependencies()
    config = load_config()
    env = backend_env(config, autostart=True)

    print("==============================================================")
    print(" RackNova Local")
    print("==============================================================")
    print(f"Nodo: {config['node_code']}")
    print(f"Empresa: {config['empresa_id']}")
    print(f"Cloud: {config['cloud_url']}")
    for ip in local_ips():
        print(f"LAN: http://{ip}:{config['api_port']}")
    print()

    os.execve(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(config["api_port"]),
        ],
        env,
    )


def diagnose(_args: argparse.Namespace) -> None:
    ensure_python_dependencies()
    config = load_config()
    print("==============================================================")
    print(" RackNova Local — Diagnóstico")
    print("==============================================================")
    print(f"Empresa: {config['empresa_id']}")
    print(f"Nodo: {config['node_code']}")
    print(f"Cloud: {config['cloud_url']}")
    print(f"Database: 127.0.0.1:{config['database_port']}")
    print(f"API port: {config['api_port']}")
    print("LAN IPs:", ", ".join(local_ips()) or "(ninguna detectada)")

    docker = run(
        docker_compose_base() + ["ps"],
        check=False,
    )
    print()
    print("Docker:")
    print((docker.stdout or "").strip() or "(sin salida)")

    try:
        req = urllib.request.Request(
            config["cloud_url"].rstrip("/") + "/docs",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            print(f"Cloud reachable: sí (HTTP {response.status})")
    except Exception as exc:
        print(f"Cloud reachable: no ({exc})")

    try:
        engine = create_engine(config["database_url"], pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        print("PostgreSQL local: OK")
    except Exception as exc:
        print(f"PostgreSQL local: ERROR ({exc})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RackNova Local Fase 2.6",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("provision", help="Crear por primera vez RackNova Local")
    p.add_argument("--cloud-url")
    p.add_argument("--empresa-id", default=DEFAULT_EMPRESA)
    p.add_argument("--sync-secret")
    p.add_argument("--node-code")
    p.add_argument("--node-name")
    p.add_argument("--db-port", type=int, default=DEFAULT_DB_PORT)
    p.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    p.add_argument("--sync-interval", type=int, default=15)

    sub.add_parser("serve", help="Iniciar FastAPI local + RackNova Sync")
    sub.add_parser("diagnose", help="Diagnóstico de PostgreSQL/LAN/Cloud")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "provision"

    if command == "provision":
        provision(args)
    elif command == "serve":
        serve(args)
    elif command == "diagnose":
        diagnose(args)
    else:
        parser.print_help()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
