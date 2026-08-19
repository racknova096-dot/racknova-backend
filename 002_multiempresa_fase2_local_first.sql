-- ============================================================
-- RACKNOVA MULTIEMPRESA - FASE 2 LOCAL-FIRST READY
-- Supabase / PostgreSQL
--
-- EJECUTAR SOLO DESPUÉS de:
--   1) aplicar el backend Fase 2 LOCAL-FIRST READY;
--   2) hacer push;
--   3) confirmar que Render inició correctamente.
--
-- OBJETIVOS:
--   - aislamiento real por empresa;
--   - separar Superadmin RackNova de owner/admin del cliente;
--   - permitir N cajas por empresa;
--   - preparar identidad de sincronización Local <-> Cloud;
--   - conservar TODOS los datos históricos.
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------
-- 0. SUPERADMIN DE PLATAFORMA
--    Un owner/admin de empresa NO puede crear otras empresas.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.racknova_platform_admins (
    id_platform_admin UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_key VARCHAR(255) NOT NULL UNIQUE,
    nombre VARCHAR(255),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Migración inicial: toma el primer admin global activo del sistema legado.
-- Después de esta fase los admins de clientes NO se agregan aquí.
DO $$
BEGIN
    IF to_regclass('public.usuario') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.racknova_platform_admins
           WHERE activo = TRUE
       )
    THEN
        INSERT INTO public.racknova_platform_admins (
            usuario_key, nombre, activo
        )
        SELECT
            u.usuario,
            COALESCE(u.nombre, u.usuario),
            TRUE
        FROM public.usuario u
        WHERE u.activo = TRUE
          AND lower(COALESCE(u.rol, '')) = 'admin'
        ORDER BY u.id_usuario
        LIMIT 1
        ON CONFLICT (usuario_key)
        DO UPDATE SET
            activo = TRUE,
            actualizado_en = NOW();
    END IF;
END $$;

-- -----------------------------------------------------------------
-- 1. CERRAR LA MIGRACIÓN LEGACY DE USUARIOS
--    Antes de crear clientes nuevos, todos los usuarios actuales quedan
--    vinculados a RackNova Principal. Así ya no necesitamos asignar
--    automáticamente usuarios nuevos a Principal.
-- -----------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.usuario') IS NOT NULL
       AND to_regclass('public.empresa_usuarios') IS NOT NULL
       AND to_regclass('public.empresas') IS NOT NULL
    THEN
        INSERT INTO public.empresa_usuarios (
            id_empresa,
            usuario_key,
            nombre_usuario,
            rol,
            activo
        )
        SELECT
            '11111111-1111-4111-8111-111111111111'::uuid,
            u.usuario,
            COALESCE(u.nombre, u.usuario),
            CASE
                WHEN lower(COALESCE(u.rol, 'viewer')) = 'admin' THEN 'owner'
                WHEN lower(COALESCE(u.rol, 'viewer')) = 'operator' THEN 'operator'
                ELSE 'viewer'
            END,
            u.activo
        FROM public.usuario u
        WHERE COALESCE(u.usuario, '') <> ''
        ON CONFLICT (id_empresa, usuario_key)
        DO UPDATE SET
            nombre_usuario = EXCLUDED.nombre_usuario,
            activo = EXCLUDED.activo,
            actualizado_en = NOW();
    END IF;
END $$;

-- -----------------------------------------------------------------
-- 2. INFRAESTRUCTURA PARA FUTURO RACKNOVA LOCAL / CLOUD
--    En Fase 2.5 el servidor local utilizará estas identidades para
--    sincronizar sin depender de IDs SERIAL distintos entre local/cloud.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.racknova_nodos (
    id_nodo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES public.empresas(id_empresa) ON DELETE CASCADE,
    codigo VARCHAR(120) NOT NULL,
    nombre VARCHAR(180) NOT NULL,
    tipo VARCHAR(30) NOT NULL DEFAULT 'LOCAL_SERVER',
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    version_app VARCHAR(80),
    ultima_conexion TIMESTAMPTZ,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT racknova_nodos_tipo_check
        CHECK (tipo IN ('CLOUD','LOCAL_SERVER','TERMINAL','EDGE')),
    CONSTRAINT racknova_nodos_empresa_codigo_unique
        UNIQUE (empresa_id, codigo)
);

CREATE INDEX IF NOT EXISTS ix_racknova_nodos_empresa
    ON public.racknova_nodos (empresa_id);

CREATE TABLE IF NOT EXISTS public.racknova_sync_outbox (
    id_evento UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES public.empresas(id_empresa) ON DELETE CASCADE,
    id_nodo UUID REFERENCES public.racknova_nodos(id_nodo) ON DELETE SET NULL,
    entidad VARCHAR(120) NOT NULL,
    entidad_sync_uuid UUID,
    operacion VARCHAR(20) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    estado VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    intentos INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enviado_en TIMESTAMPTZ,
    CONSTRAINT racknova_sync_outbox_operacion_check
        CHECK (operacion IN ('INSERT','UPDATE','DELETE','EVENT')),
    CONSTRAINT racknova_sync_outbox_estado_check
        CHECK (estado IN ('PENDING','SENDING','SYNCED','ERROR'))
);

CREATE INDEX IF NOT EXISTS ix_racknova_sync_outbox_pendiente
    ON public.racknova_sync_outbox (empresa_id, estado, creado_en);

CREATE TABLE IF NOT EXISTS public.racknova_sync_estado (
    id_estado UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES public.empresas(id_empresa) ON DELETE CASCADE,
    id_nodo UUID NOT NULL REFERENCES public.racknova_nodos(id_nodo) ON DELETE CASCADE,
    ultima_subida TIMESTAMPTZ,
    ultima_bajada TIMESTAMPTZ,
    ultimo_evento UUID,
    pendiente_subir INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT racknova_sync_estado_nodo_unique UNIQUE (id_nodo)
);

-- -----------------------------------------------------------------
-- 3. GARANTIZAR empresa_id + IDENTIDAD DE SYNC EN TABLAS OPERATIVAS
-- -----------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
    idx_empresa TEXT;
    idx_sync TEXT;
    idx_sync_fecha TEXT;
BEGIN
    FOR r IN
        SELECT t.table_name
        FROM information_schema.tables t
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
          AND t.table_name NOT IN (
              'empresas',
              'empresa_usuarios',
              'usuario',
              'racknova_platform_admins',
              'racknova_nodos',
              'racknova_sync_outbox',
              'racknova_sync_estado',
              'alembic_version',
              'schema_migrations'
          )
          AND (
              t.table_name LIKE 'pos_%'
              OR t.table_name LIKE 'producto%'
              OR t.table_name LIKE 'product%'
              OR t.table_name LIKE 'inventario%'
              OR t.table_name LIKE 'inventory%'
              OR t.table_name LIKE 'movimiento%'
              OR t.table_name LIKE 'movement%'
              OR t.table_name LIKE 'venta%'
              OR t.table_name LIKE 'sale%'
              OR t.table_name LIKE 'cliente%'
              OR t.table_name LIKE 'customer%'
              OR t.table_name LIKE 'credito%'
              OR t.table_name LIKE 'credit%'
              OR t.table_name LIKE 'abono%'
              OR t.table_name LIKE 'devolucion%'
              OR t.table_name LIKE 'catalogo%'
              OR t.table_name LIKE 'cotizacion%'
              OR t.table_name LIKE 'ubicacion%'
              OR t.table_name LIKE 'location%'
              OR t.table_name LIKE 'rack%'
              OR t.table_name LIKE 'alerta%'
              OR t.table_name LIKE 'lote%'
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS empresa_id UUID',
            r.table_name
        );
        EXECUTE format(
            'UPDATE public.%I SET empresa_id = %L::uuid WHERE empresa_id IS NULL',
            r.table_name,
            '11111111-1111-4111-8111-111111111111'
        );
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN empresa_id SET NOT NULL',
            r.table_name
        );

        idx_empresa := left('ix_' || r.table_name || '_empresa_id', 63);
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON public.%I (empresa_id)',
            idx_empresa,
            r.table_name
        );

        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = r.table_name
              AND c.conname = left('fk_' || r.table_name || '_empresa', 63)
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I '
                'FOREIGN KEY (empresa_id) REFERENCES public.empresas(id_empresa)',
                r.table_name,
                left('fk_' || r.table_name || '_empresa', 63)
            );
        END IF;

        -- Identidad estable para sincronización local/cloud.
        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS sync_uuid UUID',
            r.table_name
        );
        EXECUTE format(
            'UPDATE public.%I SET sync_uuid = gen_random_uuid() WHERE sync_uuid IS NULL',
            r.table_name
        );
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN sync_uuid SET DEFAULT gen_random_uuid()',
            r.table_name
        );
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN sync_uuid SET NOT NULL',
            r.table_name
        );

        idx_sync := left('ux_' || r.table_name || '_sync_uuid', 63);
        EXECUTE format(
            'CREATE UNIQUE INDEX IF NOT EXISTS %I ON public.%I (sync_uuid)',
            idx_sync,
            r.table_name
        );

        EXECUTE format(
            'ALTER TABLE public.%I '
            'ADD COLUMN IF NOT EXISTS sync_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()',
            r.table_name
        );
        EXECUTE format(
            'ALTER TABLE public.%I '
            'ADD COLUMN IF NOT EXISTS sync_revision BIGINT NOT NULL DEFAULT 0',
            r.table_name
        );
        EXECUTE format(
            'ALTER TABLE public.%I '
            'ADD COLUMN IF NOT EXISTS sync_origen_nodo UUID',
            r.table_name
        );

        idx_sync_fecha := left('ix_' || r.table_name || '_sync_updated_at', 63);
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON public.%I (empresa_id, sync_updated_at)',
            idx_sync_fecha,
            r.table_name
        );
    END LOOP;
END $$;

-- -----------------------------------------------------------------
-- 4. NATURAL KEYS: ÚNICOS DENTRO DE EMPRESA, NO GLOBALMENTE
-- -----------------------------------------------------------------
DO $$
DECLARE
    pair RECORD;
    con RECORD;
    idx RECORD;
    duplicated BOOLEAN;
BEGIN
    FOR pair IN
        SELECT *
        FROM (
            VALUES
                ('producto', 'sku'),
                ('producto_catalogo', 'sku'),
                ('pos_producto_configuracion', 'sku'),
                ('pos_mayoreo_menudeo', 'sku'),
                ('venta_pos', 'folio'),
                ('pos_devolucion', 'folio'),
                ('pos_abono', 'folio'),
                ('pos_venta_control', 'operacion_id')
        ) AS x(tabla, columna)
    LOOP
        IF to_regclass('public.' || pair.tabla) IS NULL THEN
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = pair.tabla
              AND column_name = pair.columna
        ) THEN
            CONTINUE;
        END IF;

        EXECUTE format(
            'SELECT EXISTS ('
            'SELECT 1 FROM public.%I '
            'WHERE %I IS NOT NULL '
            'GROUP BY empresa_id, %I HAVING COUNT(*) > 1)',
            pair.tabla, pair.columna, pair.columna
        )
        INTO duplicated;

        IF duplicated THEN
            RAISE EXCEPTION
                'Fase 2 detenida: %.% tiene duplicados dentro de la misma empresa.',
                pair.tabla, pair.columna;
        END IF;

        FOR con IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = pair.tabla
              AND c.contype = 'u'
              AND array_length(c.conkey, 1) = 1
              AND (
                  SELECT a.attname
                  FROM pg_attribute a
                  WHERE a.attrelid = t.oid
                    AND a.attnum = c.conkey[1]
              ) = pair.columna
        LOOP
            EXECUTE format(
                'ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I',
                pair.tabla, con.conname
            );
        END LOOP;

        FOR idx IN
            SELECT i.relname AS index_name
            FROM pg_index x
            JOIN pg_class t ON t.oid = x.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_class i ON i.oid = x.indexrelid
            JOIN pg_attribute a ON a.attrelid = t.oid
                               AND a.attnum = x.indkey[0]
            WHERE n.nspname = 'public'
              AND t.relname = pair.tabla
              AND x.indisunique = TRUE
              AND x.indisprimary = FALSE
              AND x.indnkeyatts = 1
              AND a.attname = pair.columna
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_constraint c
                  WHERE c.conindid = x.indexrelid
              )
        LOOP
            EXECUTE format('DROP INDEX IF EXISTS public.%I', idx.index_name);
        END LOOP;

        EXECUTE format(
            'CREATE UNIQUE INDEX IF NOT EXISTS %I '
            'ON public.%I (empresa_id, %I)',
            left('ux_' || pair.tabla || '_empresa_' || pair.columna, 63),
            pair.tabla,
            pair.columna
        );
    END LOOP;
END $$;

-- -----------------------------------------------------------------
-- 5. N CAJAS POR EMPRESA
--    No existe límite técnico fijo de Caja 1/Caja 2.
-- -----------------------------------------------------------------
DO $$
DECLARE
    duplicated BOOLEAN;
    con RECORD;
    idx RECORD;
BEGIN
    IF to_regclass('public.pos_caja') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema='public'
             AND table_name='pos_caja'
             AND column_name='nombre'
       )
    THEN
        SELECT EXISTS (
            SELECT 1
            FROM public.pos_caja
            GROUP BY empresa_id, nombre
            HAVING COUNT(*) > 1
        )
        INTO duplicated;

        IF duplicated THEN
            RAISE EXCEPTION
                'Fase 2 detenida: existen cajas con nombre duplicado dentro de una misma empresa.';
        END IF;

        FOR con IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname='public'
              AND t.relname='pos_caja'
              AND c.contype='u'
              AND array_length(c.conkey,1)=1
              AND (
                  SELECT a.attname
                  FROM pg_attribute a
                  WHERE a.attrelid=t.oid AND a.attnum=c.conkey[1]
              )='nombre'
        LOOP
            EXECUTE format(
                'ALTER TABLE public.pos_caja DROP CONSTRAINT IF EXISTS %I',
                con.conname
            );
        END LOOP;

        CREATE UNIQUE INDEX IF NOT EXISTS ux_pos_caja_empresa_nombre
            ON public.pos_caja (empresa_id, nombre);
    END IF;
END $$;

-- -----------------------------------------------------------------
-- 6. QUITAR EL DEFAULT TEMPORAL DE FASE 1
--    Un INSERT comercial sin empresa debe FALLAR, nunca caer en Principal.
-- -----------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT c.table_name
        FROM information_schema.columns c
        WHERE c.table_schema='public'
          AND c.column_name='empresa_id'
          AND c.table_name NOT IN (
              'empresas',
              'empresa_usuarios',
              'usuario',
              'racknova_platform_admins'
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN empresa_id DROP DEFAULT',
            r.table_name
        );
    END LOOP;
END $$;

-- -----------------------------------------------------------------
-- 7. DIAGNÓSTICO PERMANENTE
-- -----------------------------------------------------------------
CREATE OR REPLACE VIEW public.racknova_multiempresa_fase2_diagnostico AS
WITH tenant_tables AS (
    SELECT t.table_name
    FROM information_schema.tables t
    WHERE t.table_schema='public'
      AND t.table_type='BASE TABLE'
      AND t.table_name NOT IN (
          'racknova_nodos',
          'racknova_sync_outbox',
          'racknova_sync_estado'
      )
      AND EXISTS (
          SELECT 1
          FROM information_schema.columns c
          WHERE c.table_schema='public'
            AND c.table_name=t.table_name
            AND c.column_name='empresa_id'
      )
), global_uniques AS (
    SELECT
        cls.relname AS table_name,
        con.conname AS constraint_name,
        string_agg(att.attname, ', ' ORDER BY k.ord) AS columnas
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid=con.conrelid
    JOIN pg_namespace n ON n.oid=cls.relnamespace
    JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum,ord) ON TRUE
    JOIN pg_attribute att ON att.attrelid=con.conrelid AND att.attnum=k.attnum
    WHERE n.nspname='public'
      AND con.contype='u'
    GROUP BY cls.relname, con.conname
    HAVING bool_and(att.attname <> 'empresa_id')
)
SELECT
    tt.table_name,
    EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_schema='public'
          AND c.table_name=tt.table_name
          AND c.column_name='empresa_id'
          AND c.is_nullable='NO'
    ) AS empresa_id_not_null,
    EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_schema='public'
          AND c.table_name=tt.table_name
          AND c.column_name='empresa_id'
          AND c.column_default IS NOT NULL
    ) AS tiene_default_legacy,
    EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_schema='public'
          AND c.table_name=tt.table_name
          AND c.column_name='sync_uuid'
          AND c.is_nullable='NO'
    ) AS sync_uuid_listo,
    COALESCE((
        SELECT string_agg(
            gu.constraint_name || ' [' || gu.columnas || ']',
            '; '
        )
        FROM global_uniques gu
        WHERE gu.table_name=tt.table_name
    ), '') AS uniques_globales_a_revisar
FROM tenant_tables tt
ORDER BY tt.table_name;

COMMIT;

-- ============================================================
-- RESULTADOS / VALIDACIONES
-- ============================================================
SELECT * FROM public.racknova_multiempresa_fase2_diagnostico;

SELECT
    usuario_key,
    nombre,
    activo
FROM public.racknova_platform_admins
ORDER BY creado_en;

SELECT
    to_regclass('public.racknova_nodos') IS NOT NULL AS nodos_listos,
    to_regclass('public.racknova_sync_outbox') IS NOT NULL AS outbox_lista,
    to_regclass('public.racknova_sync_estado') IS NOT NULL AS sync_estado_listo;
