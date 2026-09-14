-- RackNova — Unidad de manejo canónica (Dashboard + Local-First)
-- Valores permitidos: pieza, bulto, caja, paquete, kg, litro.

ALTER TABLE public.producto
  ADD COLUMN IF NOT EXISTS unidad_manejo varchar(20);

ALTER TABLE public.producto_catalogo
  ADD COLUMN IF NOT EXISTS unidad_manejo varchar(20);

UPDATE public.producto p
SET unidad_manejo = CASE
  WHEN lower(trim(c.unidad_venta)) IN ('pieza','piezas','pza','pzas','unidad','unidades') THEN 'pieza'
  WHEN lower(trim(c.unidad_venta)) IN ('bulto','bultos') THEN 'bulto'
  WHEN lower(trim(c.unidad_venta)) IN ('caja','cajas') THEN 'caja'
  WHEN lower(trim(c.unidad_venta)) IN ('paquete','paquetes','paq','ristra','ristras') THEN 'paquete'
  WHEN lower(trim(c.unidad_venta)) IN ('kg','kilo','kilos','kilogramo','kilogramos') THEN 'kg'
  WHEN lower(trim(c.unidad_venta)) IN ('l','lt','lts','litro','litros') THEN 'litro'
  ELSE 'pieza'
END
FROM public.pos_producto_configuracion c
WHERE c.empresa_id = p.empresa_id
  AND c.sku = p.sku
  AND (p.unidad_manejo IS NULL OR btrim(p.unidad_manejo) = '');

UPDATE public.producto
SET unidad_manejo = 'pieza'
WHERE unidad_manejo IS NULL OR btrim(unidad_manejo) = '';

UPDATE public.producto_catalogo pc
SET unidad_manejo = p.unidad_manejo
FROM public.producto p
WHERE p.empresa_id = pc.empresa_id
  AND p.sku = pc.sku
  AND (pc.unidad_manejo IS NULL OR btrim(pc.unidad_manejo) = '');

UPDATE public.producto_catalogo
SET unidad_manejo = 'pieza'
WHERE unidad_manejo IS NULL OR btrim(unidad_manejo) = '';

UPDATE public.pos_producto_configuracion c
SET unidad_venta = p.unidad_manejo,
    fecha_actualizacion = CURRENT_TIMESTAMP
FROM public.producto p
WHERE p.empresa_id = c.empresa_id
  AND p.sku = c.sku
  AND c.unidad_venta IS DISTINCT FROM p.unidad_manejo;

DO $$
BEGIN
  IF to_regclass('public.pos_mayoreo_menudeo') IS NOT NULL THEN
    UPDATE public.pos_mayoreo_menudeo m
    SET unidad = p.unidad_manejo,
        actualizado_en = CURRENT_TIMESTAMP
    FROM public.producto p
    WHERE p.empresa_id = m.empresa_id
      AND p.sku = m.sku
      AND m.unidad IS DISTINCT FROM p.unidad_manejo;
  END IF;
END $$;

ALTER TABLE public.producto
  ALTER COLUMN unidad_manejo SET DEFAULT 'pieza',
  ALTER COLUMN unidad_manejo SET NOT NULL;

ALTER TABLE public.producto_catalogo
  ALTER COLUMN unidad_manejo SET DEFAULT 'pieza',
  ALTER COLUMN unidad_manejo SET NOT NULL;

ALTER TABLE public.producto
  DROP CONSTRAINT IF EXISTS ck_producto_unidad_manejo;

ALTER TABLE public.producto
  ADD CONSTRAINT ck_producto_unidad_manejo
  CHECK (unidad_manejo IN ('pieza','bulto','caja','paquete','kg','litro'));

ALTER TABLE public.producto_catalogo
  DROP CONSTRAINT IF EXISTS ck_producto_catalogo_unidad_manejo;

ALTER TABLE public.producto_catalogo
  ADD CONSTRAINT ck_producto_catalogo_unidad_manejo
  CHECK (unidad_manejo IN ('pieza','bulto','caja','paquete','kg','litro'));
