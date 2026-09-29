-- Feedback RRHH 2026-09-12: instituciones inventan códigos de calendario nuevos (ej. "P" =
-- Planificación) que quedan CODIGO_DESCONOCIDO para siempre si nadie los clasifica. La tabla
-- catalogo_codigo_tipo_dia_fuente ya existía para esto; solo le faltaba responsable/motivo/fecha
-- para que la clasificación quede trazable, igual que el resto de decisiones del recorrido web.
ALTER TABLE catalogo_codigo_tipo_dia_fuente ADD COLUMN creado_por bigint NULL REFERENCES usuario(usuario_id);
ALTER TABLE catalogo_codigo_tipo_dia_fuente ADD COLUMN creado_en timestamptz NOT NULL DEFAULT now();
ALTER TABLE catalogo_codigo_tipo_dia_fuente ADD COLUMN motivo text NULL;
