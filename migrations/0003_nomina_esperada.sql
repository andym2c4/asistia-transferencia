-- Nomina esperada del mes ante encargaturas y reemplazos.
-- Origen: docs/decisions/2026-09-10-nomina-esperada-encargaturas-reemplazos.md §6.
-- Confirmado por RRHH el 2026-09-10 (§8 de ese documento), incluye el caso de reemplazo a mitad
-- de mes que RRHH senalo como motivo de la regla.

-- Indica si se espera presencia fisica del trabajador de este vinculo, en su propia plaza, en la
-- fecha dada. NULL si el vinculo no tiene trabajador (situacion_laboral='VACANTE').
CREATE OR REPLACE FUNCTION fn_vinculo_presencia_esperada(
    p_vinculo_trabajador_ie_id bigint,
    p_fecha date
) RETURNS boolean AS $$
DECLARE
    v vinculo_trabajador_ie%ROWTYPE;
    hay_reemplazo boolean;
    hay_encargatura_en_otra_plaza boolean;
BEGIN
    SELECT * INTO v FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id = p_vinculo_trabajador_ie_id;

    IF v.trabajador_id IS NULL THEN
        RETURN NULL;
    END IF;

    IF NOT (daterange(COALESCE(v.fecha_inicio,'-infinity'::date), COALESCE(v.fecha_fin,'infinity'::date), '[]') @> p_fecha) THEN
        RETURN false;
    END IF;

    -- Regla 1: existe un POR_REEMPLAZO vigente en la misma plaza ese dia.
    SELECT EXISTS (
        SELECT 1 FROM vinculo_trabajador_ie r
        WHERE r.plaza_id = v.plaza_id
          AND r.tipo_registro = 'POR_REEMPLAZO'
          AND r.vinculo_trabajador_ie_id <> v.vinculo_trabajador_ie_id
          AND daterange(COALESCE(r.fecha_inicio,'-infinity'::date), COALESCE(r.fecha_fin,'infinity'::date), '[]') @> p_fecha
    ) INTO hay_reemplazo;
    IF hay_reemplazo THEN
        RETURN false;
    END IF;

    -- Regla 2: el mismo trabajador tiene un vinculo ENCARGADO vigente en otra plaza ese dia.
    SELECT EXISTS (
        SELECT 1 FROM vinculo_trabajador_ie e
        WHERE e.trabajador_id = v.trabajador_id
          AND e.vinculo_trabajador_ie_id <> v.vinculo_trabajador_ie_id
          AND e.situacion_laboral = 'ENCARGADO'
          AND (e.plaza_id IS DISTINCT FROM v.plaza_id)
          AND daterange(COALESCE(e.fecha_inicio,'-infinity'::date), COALESCE(e.fecha_fin,'infinity'::date), '[]') @> p_fecha
    ) INTO hay_encargatura_en_otra_plaza;

    RETURN NOT hay_encargatura_en_otra_plaza;
END;
$$ LANGUAGE plpgsql STABLE;
