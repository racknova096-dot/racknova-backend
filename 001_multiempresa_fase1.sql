-- ============================================================
-- RACKNOVA MULTIEMPRESA - FASE 1
-- Supabase / PostgreSQL
-- Idempotente: puede ejecutarse más de una vez.
--
-- Objetivo:
-- 1) Crear empresas y membresías usuario-empresa.
-- 2) Crear "RackNova Principal" y conservar allí los datos actuales.
-- 3) Añadir empresa_id a tablas comerciales conocidas sin romper código legacy.
-- 4) Preparar mayoreo para que el mismo SKU pueda existir en varias empresas.
--
-- IMPORTANTE:
-- Esta fase NO activa todavía el selector de empresa en el dashboard.
-- Los módulos se irán tenantizando antes de permitir cambiar de empresa.
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.empresas (
    id_empresa UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre VARCHAR(150) NOT NULL,
    slug VARCHAR(120) NOT NULL UNIQUE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    plan VARCHAR(30) NOT NULL DEFAULT 'basic',
    moneda VARCHAR(10) NOT NULL DEFAULT 'MXN',
    zona_horaria VARCHAR(80) NOT NULL DEFAULT 'America/Mexico_City',
    rfc VARCHAR(20),
    razon_social VARCHAR(255),
    logo_url TEXT,
    proveedor_facturacion VARCHAR(50),
    proveedor_organizacion_id VARCHAR(255),
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.empresa_usuarios (
    id_membresia UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_empresa UUID NOT NULL REFERENCES public.empresas(id_empresa) ON DELETE CASCADE,
    usuario_key VARCHAR(255) NOT NULL,
    nombre_usuario VARCHAR(255),
    rol VARCHAR(30) NOT NULL DEFAULT 'viewer',
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT empresa_usuarios_rol_check
        CHECK (rol IN ('owner', 'admin', 'operator', 'viewer')),
    CONSTRAINT empresa_usuarios_empresa_usuario_unique
        UNIQUE (id_empresa, usuario_key)
);

CREATE INDEX IF NOT EXISTS ix_empresa_usuarios_usuario_key
    ON public.empresa_usuarios(usuario_key);
CREATE INDEX IF NOT EXISTS ix_empresa_usuarios_empresa_activo
    ON public.empresa_usuarios(id_empresa, activo);

-- UUID fijo para que la migración sea determinística y el backend pueda
-- reconocer la empresa que contiene todos los datos históricos actuales.
INSERT INTO public.empresas (
    id_empresa, nombre, slug, activo, plan, moneda, zona_horaria
)
VALUES (
    '11111111-1111-4111-8111-111111111111'::uuid,
    'RackNova Principal',
    'racknova-principal',
    TRUE,
    'legacy',
    'MXN',
    'America/Mexico_City'
)
ON CONFLICT (id_empresa) DO UPDATE SET
    nombre = EXCLUDED.nombre,
    activo = TRUE,
    actualizado_en = NOW();

-- Añadir empresa_id a tablas de negocio existentes. Se usa DEFAULT a la
-- empresa principal para que el código actual siga funcionando mientras
-- cada módulo se adapta a multiempresa.
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT t.table_name
        FROM information_schema.tables t
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
          AND t.table_name NOT IN (
              'empresas',
              'empresa_usuarios',
              'alembic_version',
              'schema_migrations'
          )
          AND (
              left(t.table_name, 4) = 'pos_'
              OR t.table_name LIKE 'producto%'
              OR t.table_name LIKE 'inventario%'
              OR t.table_name LIKE 'movimiento%'
              OR t.table_name LIKE 'venta%'
              OR t.table_name LIKE 'cliente%'
              OR t.table_name LIKE 'credito%'
              OR t.table_name LIKE 'crédito%'
              OR t.table_name LIKE 'abono%'
              OR t.table_name LIKE 'cotizacion%'
              OR t.table_name LIKE 'cotización%'
              OR t.table_name LIKE 'catalogo%'
              OR t.table_name LIKE 'catálogo%'
              OR t.table_name LIKE 'ubicacion%'
              OR t.table_name LIKE 'ubicación%'
              OR t.table_name LIKE 'rack%'
              OR t.table_name LIKE 'alerta%'
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS empresa_id UUID',
            r.table_name
        );

        EXECUTE format(
            'UPDATE public.%I SET empresa_id = $1 WHERE empresa_id IS NULL',
            r.table_name
        ) USING '11111111-1111-4111-8111-111111111111'::uuid;

        -- Default temporal: evita romper inserciones del código legacy.
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN empresa_id SET DEFAULT %L::uuid',
            r.table_name,
            '11111111-1111-4111-8111-111111111111'
        );

        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN empresa_id SET NOT NULL',
            r.table_name
        );

        BEGIN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (empresa_id) REFERENCES public.empresas(id_empresa)',
                r.table_name,
                left('fk_' || r.table_name || '_empresa', 63)
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END;

        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON public.%I(empresa_id)',
            left('ix_' || r.table_name || '_empresa', 63),
            r.table_name
        );
    END LOOP;
END $$;

-- Mayoreo v4 tenía SKU globalmente UNIQUE. Para multiempresa debe ser
-- UNIQUE (empresa_id, sku).
DO $$
DECLARE
    c RECORD;
BEGIN
    IF to_regclass('public.pos_mayoreo_menudeo') IS NULL THEN
        RETURN;
    END IF;

    FOR c IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        WHERE nsp.nspname = 'public'
          AND rel.relname = 'pos_mayoreo_menudeo'
          AND con.contype = 'u'
          AND (
              SELECT array_agg(att.attname ORDER BY x.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY AS x(attnum, ordinality)
              JOIN pg_attribute att
                ON att.attrelid = rel.oid
               AND att.attnum = x.attnum
          ) = ARRAY['sku']::name[]
    LOOP
        EXECUTE format(
            'ALTER TABLE public.pos_mayoreo_menudeo DROP CONSTRAINT %I',
            c.conname
        );
    END LOOP;

    CREATE UNIQUE INDEX IF NOT EXISTS ux_pos_mayoreo_empresa_sku
        ON public.pos_mayoreo_menudeo(empresa_id, sku);
END $$;

-- Vista de diagnóstico: permite revisar qué tablas comerciales ya tienen
-- empresa_id y cuáles faltan por adaptar.
CREATE OR REPLACE VIEW public.racknova_multiempresa_diagnostico AS
SELECT
    t.table_name,
    EXISTS (
        SELECT 1
        FROM information_schema.columns c
        WHERE c.table_schema = t.table_schema
          AND c.table_name = t.table_name
          AND c.column_name = 'empresa_id'
    ) AS tiene_empresa_id
FROM information_schema.tables t
WHERE t.table_schema = 'public'
  AND t.table_type = 'BASE TABLE'
ORDER BY t.table_name;

COMMIT;

-- Comprobación rápida:
SELECT id_empresa, nombre, slug, activo, plan
FROM public.empresas
ORDER BY creado_en;

SELECT table_name, tiene_empresa_id
FROM public.racknova_multiempresa_diagnostico
ORDER BY table_name;
