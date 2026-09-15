# RackNova Backend — Ambientes y flujo de despliegue

## Producción
- Rama: `main`
- Backend usado por clientes y RackNova Local.
- Debe apuntar únicamente a la base Supabase de producción.
- El instalador y RackNova Local no deben consumir builds de `develop` ni `feature/*`.

## Staging / Pruebas
- Rama: `develop`
- Debe desplegarse como servicio Render independiente.
- Debe usar una base Supabase independiente de staging.

## Desarrollo
Crear ramas desde `develop`:

```text
feature/<nombre>
fix/<nombre>
```

Flujo:

```text
feature/* o fix/*
        ↓
     develop
        ↓
 Render STAGING + Supabase STAGING
        ↓
 validación funcional
        ↓
       main
        ↓
 Render PROD + Supabase PROD
```

## Reglas de seguridad
- No ejecutar migraciones experimentales contra Supabase PROD.
- No usar credenciales de producción en staging.
- No promover cambios a `main` sin validar el flujo Cloud/Local correspondiente.
- Mantener `main` como fuente estable para instaladores de clientes.

## Rollback
Baseline de producción creado el 2026-09-15:

`backup/production-baseline-20260915`
