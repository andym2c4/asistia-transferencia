-- Reparacion: `catalogo_codigo_tipo_dia_fuente` aparecio sin sus 3 filas semilla (L/G/D) en la
-- base de pruebas (db_test) -- causa no reproducida, el piloto (db) las conservaba intactas.
-- Idempotente (ON CONFLICT DO NOTHING) para no duplicar donde ya existen.
INSERT INTO catalogo_codigo_tipo_dia_fuente (familia_formato, codigo_raw, tipo_dia_id)
SELECT 'UGEL_LUYA_CALENDARIO_2026', codigo_raw, tipo_dia_id
FROM (VALUES ('L', 'LECTIVO'), ('G', 'GESTION'), ('D', 'NO_LECTIVO_NI_GESTION')) AS m(codigo_raw, codigo_interno)
JOIN catalogo_tipo_dia USING (codigo_interno)
ON CONFLICT DO NOTHING;
