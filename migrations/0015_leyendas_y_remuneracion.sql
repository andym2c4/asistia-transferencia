-- Dos clasificaciones independientes y locales a la versión documental.
-- Los tres tipos anteriores permanecen como agrupación técnica de actividad.
ALTER TABLE calendarizacion_version ADD COLUMN clasificacion_codigos jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK (jsonb_typeof(clasificacion_codigos) = 'object');
ALTER TABLE reporte_asistencia ADD COLUMN clasificacion_codigos jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK (jsonb_typeof(clasificacion_codigos) = 'object');
ALTER TABLE dia_calendarizacion ADD COLUMN codigo_interpretado text NULL;
ALTER TABLE dia_calendarizacion ADD COLUMN evidencia_interpretacion jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE asistencia_dia ADD COLUMN codigo_interpretado text NULL;
ALTER TABLE asistencia_dia ADD COLUMN evidencia_interpretacion jsonb NOT NULL DEFAULT '{}'::jsonb;
-- El resultado del cruce se congela en consolidado_dre_detalle.fuente_calculo.
-- es_remunerado admite TRUE/FALSE; NULL significa clasificación aún no definida,
-- no una tercera categoría de remuneración ni un valor NO remunerado por defecto.
