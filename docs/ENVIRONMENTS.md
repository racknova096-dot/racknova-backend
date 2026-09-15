# RackNova Backend — flujo de producción sin costo adicional

## Producción
- Rama: `main`.
- Render PROD + Supabase PROD permanecen sin cambios.
- RackNova Local de clientes solo debe consumir la versión estable.
- No ejecutar pruebas ni migraciones experimentales contra producción.

## Desarrollo
- Rama de integración: `develop`.
- Cambios nuevos: `feature/*` o `fix/*`, creados desde `develop`.

No se crea un segundo Render ni un segundo Supabase.

## Staging local aislado — costo $0

El repositorio incluye:

```text
scripts/start-dev-zero-cost.ps1
scripts/stop-dev-zero-cost.ps1
```

El script de inicio:
1. reutiliza los binarios PostgreSQL instalados con RackNova;
2. crea un cluster PostgreSQL completamente separado en `%LOCALAPPDATA%\RackNovaDev`;
3. usa el puerto `54339`, distinto al RackNova Local real;
4. crea la base `racknova_dev`;
5. usa credenciales locales generadas automáticamente;
6. crea el esquema y aplica las migraciones del repositorio;
7. inicia FastAPI en `http://127.0.0.1:8010`;
8. fuerza `RACKNOVA_SYNC_AUTOSTART=false`;
9. deja vacíos `RACKNOVA_CLOUD_URL` y `RACKNOVA_SYNC_SECRET`.

Por diseño, este entorno no sincroniza con RackNova Cloud y no utiliza Supabase PROD.

### Iniciar

Desde PowerShell, dentro del backend en rama `develop`:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\start-dev-zero-cost.ps1
```

Credenciales iniciales de la base nueva:

```text
usuario:  admin@racknova.com
password: admin123
```

Son únicamente para desarrollo local.

### Detener PostgreSQL dev

```powershell
.\scripts\stop-dev-zero-cost.ps1
```

## Flujo de promoción

```text
feature/* o fix/*
        ↓
     develop
        ↓
Dashboard local + Backend local + PostgreSQL dev
        ↓
validación funcional
        ↓
aprobación explícita
        ↓
       main
        ↓
Render PROD + Supabase PROD
```

## Rollback
Checkpoint de producción:

`backup/production-baseline-20260915`
