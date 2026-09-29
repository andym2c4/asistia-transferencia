-- No reescribe fuentes ni decisiones existentes. Corrige nuevas reclasificaciones.
CREATE OR REPLACE FUNCTION fn_asistencia_dia_resolver_hecho() RETURNS trigger AS $$
DECLARE
    v_trabajador_id bigint; v_ie_id bigint; v_rol_id smallint;
    v_hecho_id uuid; v_hecho_estado smallint;
BEGIN
    IF NEW.estado_captura NOT IN ('REGISTRADO', 'DERIVADO') THEN
        RETURN NULL;
    END IF;

    -- La actualización del enlace dispara el trigger una segunda vez.
    IF NEW.hecho_asistencia_dia_id IS NOT NULL THEN
        RETURN NULL;
    END IF;

    SELECT ter.trabajador_id, ra.institucion_educativa_id, ter.rol_laboral_id
    INTO v_trabajador_id, v_ie_id, v_rol_id
    FROM trabajador_en_reporte ter
    JOIN reporte_asistencia ra ON ra.reporte_asistencia_id = ter.reporte_asistencia_id
    WHERE ter.trabajador_en_reporte_id = NEW.trabajador_en_reporte_id;

    IF v_trabajador_id IS NULL OR v_ie_id IS NULL OR v_rol_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT hecho_asistencia_dia_id, estado_asistencia_id INTO v_hecho_id, v_hecho_estado
    FROM hecho_asistencia_dia
    WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id = v_ie_id
      AND rol_laboral_id = v_rol_id AND fecha = NEW.fecha
    FOR UPDATE;

    IF v_hecho_id IS NULL THEN
        INSERT INTO hecho_asistencia_dia (trabajador_id, institucion_educativa_id, rol_laboral_id, fecha, estado_asistencia_id)
        VALUES (v_trabajador_id, v_ie_id, v_rol_id, NEW.fecha, NEW.estado_asistencia_id)
        RETURNING hecho_asistencia_dia_id INTO v_hecho_id;
    ELSIF v_hecho_estado IS DISTINCT FROM NEW.estado_asistencia_id AND NOT EXISTS (
        SELECT 1 FROM asistencia_dia otra
        WHERE otra.hecho_asistencia_dia_id = v_hecho_id
          AND otra.asistencia_dia_id <> NEW.asistencia_dia_id
          AND otra.estado_captura IN ('REGISTRADO', 'DERIVADO')
    ) THEN
        -- La única evidencia es esta misma fila reclasificada, no otra fuente.
        UPDATE hecho_asistencia_dia
        SET estado_asistencia_id = NEW.estado_asistencia_id,
            es_disputado = false, actualizado_en = now()
        WHERE hecho_asistencia_dia_id = v_hecho_id;
    ELSIF v_hecho_estado IS DISTINCT FROM NEW.estado_asistencia_id THEN
        UPDATE hecho_asistencia_dia SET es_disputado = true, actualizado_en = now() WHERE hecho_asistencia_dia_id = v_hecho_id;
        INSERT INTO validacion_reporte (reporte_asistencia_id, trabajador_en_reporte_id, fecha, codigo_regla, severidad, mensaje, evidencia)
        SELECT ter.reporte_asistencia_id, NEW.trabajador_en_reporte_id, NEW.fecha, 'ASISTENCIA_CONTRADICTORIA', 'ERROR',
               format('Evidencia contradictoria para trabajador %s, IE %s, rol %s, fecha %s', v_trabajador_id, v_ie_id, v_rol_id, NEW.fecha),
               jsonb_build_object('hecho_asistencia_dia_id', v_hecho_id, 'estado_previo', v_hecho_estado, 'estado_nuevo', NEW.estado_asistencia_id)
        FROM trabajador_en_reporte ter WHERE ter.trabajador_en_reporte_id = NEW.trabajador_en_reporte_id
          AND NOT EXISTS (
              SELECT 1 FROM validacion_reporte v
              WHERE v.reporte_asistencia_id = ter.reporte_asistencia_id
                AND v.trabajador_en_reporte_id = NEW.trabajador_en_reporte_id
                AND v.fecha = NEW.fecha
                AND v.codigo_regla = 'ASISTENCIA_CONTRADICTORIA'
                AND v.estado = 'PENDIENTE'
                AND v.evidencia = jsonb_build_object(
                    'hecho_asistencia_dia_id', v_hecho_id,
                    'estado_previo', v_hecho_estado,
                    'estado_nuevo', NEW.estado_asistencia_id)
          );
    END IF;

    UPDATE asistencia_dia SET hecho_asistencia_dia_id = v_hecho_id WHERE asistencia_dia_id = NEW.asistencia_dia_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
