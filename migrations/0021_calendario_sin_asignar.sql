-- Corrección explícita de una lectura: no equivale a vacío recibido ni a no aplica.
-- Conserva la regla existente: solo REGISTRADO tiene tipo_dia_id.
ALTER TABLE dia_calendarizacion
    DROP CONSTRAINT dia_calendarizacion_estado_captura_check;
ALTER TABLE dia_calendarizacion
    ADD CONSTRAINT dia_calendarizacion_estado_captura_check
    CHECK (estado_captura IN ('REGISTRADO', 'VACIO', 'ILEGIBLE',
                             'CODIGO_DESCONOCIDO', 'NO_APLICA', 'SIN_ASIGNAR'));
