-- Excel usa A1; OCR necesita página, tabla y fila. No truncar evidencia documental.
ALTER TABLE asistencia_dia ALTER COLUMN celda_origen TYPE text;
ALTER TABLE dia_calendarizacion ALTER COLUMN celda_origen TYPE text;
-- La fuente de otro año no es una versión padre de la misma calendarización.
ALTER TABLE calendarizacion_version ADD COLUMN fuente_derivada_id uuid REFERENCES calendarizacion_version;
ALTER TABLE reporte_asistencia DROP CONSTRAINT reporte_asistencia_tipo_fuente_check;
ALTER TABLE reporte_asistencia ADD CONSTRAINT reporte_asistencia_tipo_fuente_check
 CHECK (tipo_fuente IN ('EXCEL_NATIVO','PDF_NATIVO','PDF_ESCANEADO','FOTO','DOCX_NATIVO'));
