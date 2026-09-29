-- Vigencia del vinculo trabajador-institucion y camino automatico de reasignacion.
-- Origen: docs/decisions/2026-09-10-vigencia-vinculo-trabajador-institucion.md §7-B, §12, §13.
-- Confirmado por RRHH el 2026-09-10 (§12 de ese documento, caso real P02 en Tactamal).

CREATE TABLE vinculo_trabajador_ie_confirmacion (
    vinculo_trabajador_ie_confirmacion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vinculo_trabajador_ie_id bigint NOT NULL REFERENCES vinculo_trabajador_ie(vinculo_trabajador_ie_id),
    accion varchar(20) NOT NULL,
    precision_fecha varchar(20) NOT NULL,
    fecha_exacta date NULL,
    periodo_confirmado date NULL,
    trabajador_en_reporte_id uuid NOT NULL REFERENCES trabajador_en_reporte(trabajador_en_reporte_id),
    vinculo_trabajador_ie_relacionado_id bigint NULL REFERENCES vinculo_trabajador_ie(vinculo_trabajador_ie_id),
    motivo text NOT NULL,
    origen varchar(30) NOT NULL,
    confirmado_por bigint NULL REFERENCES usuario(usuario_id),
    confirmado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (accion IN ('CONFIRMA_INICIO','CONFIRMA_CIERRE')),
    CHECK (precision_fecha IN ('FECHA_EXACTA','PERIODO_MENSUAL')),
    CHECK ((precision_fecha = 'FECHA_EXACTA') = (fecha_exacta IS NOT NULL)),
    CHECK ((precision_fecha = 'PERIODO_MENSUAL') = (periodo_confirmado IS NOT NULL)),
    CHECK (periodo_confirmado IS NULL OR periodo_confirmado = date_trunc('month', periodo_confirmado)::date),
    CHECK (vinculo_trabajador_ie_relacionado_id IS NULL OR vinculo_trabajador_ie_relacionado_id <> vinculo_trabajador_ie_id),
    CHECK (origen IN ('MANUAL_RRHH','AUTOMATICO_IMPORTACION')),
    CHECK ((origen = 'MANUAL_RRHH') = (confirmado_por IS NOT NULL))
);
CREATE INDEX ix_vinculo_confirmacion_vinculo
    ON vinculo_trabajador_ie_confirmacion (vinculo_trabajador_ie_id, confirmado_en);
CREATE INDEX ix_vinculo_confirmacion_relacionado
    ON vinculo_trabajador_ie_confirmacion (vinculo_trabajador_ie_relacionado_id)
    WHERE vinculo_trabajador_ie_relacionado_id IS NOT NULL;

-- Idempotencia (decision de vigencia §13.4): una reasignacion automatica para el mismo vinculo y
-- el mismo periodo no se duplica, sin importar cuantas veces se reimporte el reporte que la
-- origino. Las confirmaciones manuales no tienen esta restriccion (pregunta abierta §9.3: no esta
-- claro si una reconfirmacion manual repetida es redundante o evidencia adicional legitima).
CREATE UNIQUE INDEX uq_confirmacion_automatica_periodo
    ON vinculo_trabajador_ie_confirmacion (vinculo_trabajador_ie_id, accion, periodo_confirmado)
    WHERE origen = 'AUTOMATICO_IMPORTACION' AND precision_fecha = 'PERIODO_MENSUAL';

CREATE TRIGGER trg_vinculo_confirmacion_append_only
    BEFORE UPDATE OR DELETE ON vinculo_trabajador_ie_confirmacion
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();

-- Aplica una fecha exacta al vinculo canonico; el periodo mensual nunca la toca (evita inventar
-- una fecha que RRHH no confirmo). Rechaza una fecha exacta contradictoria en vez de sobrescribir.
CREATE OR REPLACE FUNCTION fn_vinculo_confirmacion_aplica_fecha() RETURNS trigger AS $$
DECLARE
    actual date;
BEGIN
    IF NEW.precision_fecha = 'PERIODO_MENSUAL' THEN
        RETURN NEW;
    END IF;

    IF NEW.accion = 'CONFIRMA_INICIO' THEN
        SELECT fecha_inicio INTO actual FROM vinculo_trabajador_ie
         WHERE vinculo_trabajador_ie_id = NEW.vinculo_trabajador_ie_id;
        IF actual IS NOT NULL AND actual <> NEW.fecha_exacta THEN
            RAISE EXCEPTION 'vinculo % ya tiene fecha_inicio % confirmada; % es contradictoria',
                NEW.vinculo_trabajador_ie_id, actual, NEW.fecha_exacta;
        END IF;
        UPDATE vinculo_trabajador_ie SET fecha_inicio = NEW.fecha_exacta
         WHERE vinculo_trabajador_ie_id = NEW.vinculo_trabajador_ie_id;
    ELSE
        SELECT fecha_fin INTO actual FROM vinculo_trabajador_ie
         WHERE vinculo_trabajador_ie_id = NEW.vinculo_trabajador_ie_id;
        IF actual IS NOT NULL AND actual <> NEW.fecha_exacta THEN
            RAISE EXCEPTION 'vinculo % ya tiene fecha_fin % confirmada; % es contradictoria',
                NEW.vinculo_trabajador_ie_id, actual, NEW.fecha_exacta;
        END IF;
        UPDATE vinculo_trabajador_ie SET fecha_fin = NEW.fecha_exacta
         WHERE vinculo_trabajador_ie_id = NEW.vinculo_trabajador_ie_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_vinculo_confirmacion_aplica_fecha
    AFTER INSERT ON vinculo_trabajador_ie_confirmacion
    FOR EACH ROW EXECUTE FUNCTION fn_vinculo_confirmacion_aplica_fecha();

-- Vigencia conocida por vinculo, combinando fecha exacta (cuando existe) con el periodo mas
-- antiguo/reciente confirmado, sin inventar fechas. inicio_es_fecha_exacta/fin_es_fecha_exacta
-- distinguen una fecha real de una aproximacion por periodo.
CREATE VIEW vinculo_vigencia_conocida AS
SELECT
    v.vinculo_trabajador_ie_id,
    COALESCE(v.fecha_inicio, ini.primer_periodo)              AS inicio_conocido_desde,
    (v.fecha_inicio IS NOT NULL)                               AS inicio_es_fecha_exacta,
    COALESCE(v.fecha_fin, cie.ultimo_periodo_fin)              AS fin_conocido_hasta,
    (v.fecha_fin IS NOT NULL)                                  AS fin_es_fecha_exacta
FROM vinculo_trabajador_ie v
LEFT JOIN (
    SELECT vinculo_trabajador_ie_id, MIN(periodo_confirmado) AS primer_periodo
    FROM vinculo_trabajador_ie_confirmacion
    WHERE accion = 'CONFIRMA_INICIO' AND precision_fecha = 'PERIODO_MENSUAL'
    GROUP BY vinculo_trabajador_ie_id
) ini ON ini.vinculo_trabajador_ie_id = v.vinculo_trabajador_ie_id
LEFT JOIN (
    SELECT vinculo_trabajador_ie_id,
           (MAX(periodo_confirmado) + INTERVAL '1 month' - INTERVAL '1 day')::date AS ultimo_periodo_fin
    FROM vinculo_trabajador_ie_confirmacion
    WHERE accion = 'CONFIRMA_CIERRE' AND precision_fecha = 'PERIODO_MENSUAL'
    GROUP BY vinculo_trabajador_ie_id
) cie ON cie.vinculo_trabajador_ie_id = v.vinculo_trabajador_ie_id;

-- Revision SQL 2026-09-10: cierra una race condition real, demostrada con 2 sesiones psql
-- genuinamente concurrentes (sesion B corrio y confirmo completo mientras A seguia sin confirmar).
-- Los Casos 2/3 de fn_resolver_vinculo_por_reporte (mas abajo) crean un vinculo POR_REPORTE nuevo
-- tras un SELECT que no toma ningun lock; el EXCLUDE de vinculo_trabajador_ie (migrations/0001,
-- por plaza_id) no cubre esta forma de fila porque todo POR_REPORTE tiene plaza_id NULL, y NULL
-- nunca colisiona consigo mismo. Sin este indice, 2 llamadas concurrentes para el mismo
-- trabajador+institucion crean 2 filas duplicadas (reproducido: ids reales 1566/1567). Con el
-- indice, la segunda insercion espera a que la primera termine (comportamiento documentado de
-- INSERT...ON CONFLICT de Postgres ante un conflicto con una fila aun no confirmada) y el codigo
-- de abajo la reconoce en vez de duplicarla.
CREATE UNIQUE INDEX uq_vinculo_por_reporte_activo
    ON vinculo_trabajador_ie (trabajador_id, institucion_educativa_id)
    WHERE tipo_registro = 'POR_REPORTE' AND fecha_fin IS NULL;

-- Camino automatico (decision de vigencia §13.5): se ejecuta al resolver
-- trabajador_en_reporte.vinculo_trabajador_ie_id para una fila con trabajador_id ya identificado.
CREATE OR REPLACE FUNCTION fn_resolver_vinculo_por_reporte(p_trabajador_en_reporte_id uuid)
RETURNS bigint AS $$
DECLARE
    v_trabajador_id bigint;
    v_reporte_ie bigint;
    v_periodo date;
    v_rol_id smallint;
    v_vinculo_en_b bigint;
    v_tipo_registro_b varchar(20);
    v_vinculo_nexus_otra bigint;
    v_ie_anterior bigint;
    v_nuevo_vinculo bigint;
    v_situacion_reportada varchar(30);
    v_otros_candidatos bigint[];
BEGIN
    SELECT ter.trabajador_id, ra.institucion_educativa_id, ra.periodo, ter.rol_laboral_id
    INTO v_trabajador_id, v_reporte_ie, v_periodo, v_rol_id
    FROM trabajador_en_reporte ter
    JOIN reporte_asistencia ra ON ra.reporte_asistencia_id = ter.reporte_asistencia_id
    WHERE ter.trabajador_en_reporte_id = p_trabajador_en_reporte_id;

    IF v_trabajador_id IS NULL OR v_reporte_ie IS NULL THEN
        RETURN NULL;
    END IF;

    -- Caso 1: el trabajador ya tiene un vinculo con la institucion del reporte.
    -- fecha_inicio se compara contra el ULTIMO dia del periodo (mismo criterio que
    -- vinculo_vigencia_conocida.ultimo_periodo_fin), simetrico con como fecha_fin ya se compara
    -- contra el PRIMER dia: un vinculo que arranca a mitad de mes sigue vigente para ese periodo.
    -- Revision de modelo 2026-09-10: sin este filtro, un vinculo con inicio futuro (que si aparece
    -- en datos reales, algunos cortes NEXUS traen fecha_inicio poblada) podia calzar igual.
    SELECT vinculo_trabajador_ie_id, tipo_registro INTO v_vinculo_en_b, v_tipo_registro_b
    FROM vinculo_trabajador_ie
    WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id = v_reporte_ie
      AND (fecha_inicio IS NULL OR fecha_inicio <= (v_periodo + INTERVAL '1 month' - INTERVAL '1 day')::date)
      AND (fecha_fin IS NULL OR fecha_fin >= v_periodo)
    ORDER BY (tipo_registro <> 'POR_REPORTE') DESC, vinculo_trabajador_ie_id
    LIMIT 1;

    IF v_vinculo_en_b IS NOT NULL THEN
        -- Ambiguedad real confirmada por RRHH 2026-09-10 (revision de modelo): un trabajador
        -- puede tener 2+ vinculos concurrentes legitimos en la misma institucion (titular +
        -- encargatura en otra plaza, o CUADRO_DE_HORAS + POR_REEMPLAZO simultaneos -- confirmado
        -- con 30 pares reales en datos NEXUS ya importados). El desempate de arriba sigue
        -- eligiendo uno solo (no hay forma de saber, desde una sola marca diaria de asistencia,
        -- a cual de los 2 corresponde), pero se alerta cuando hay otro candidato con la misma
        -- prioridad (mismo nivel de tipo_registro<>'POR_REPORTE') para que RRHH lo revise --
        -- mismo patron "automatico pero visible" ya confirmado para los Casos 2/3.
        SELECT array_agg(vinculo_trabajador_ie_id) INTO v_otros_candidatos
        FROM vinculo_trabajador_ie
        WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id = v_reporte_ie
          AND vinculo_trabajador_ie_id <> v_vinculo_en_b
          AND (tipo_registro <> 'POR_REPORTE') = (v_tipo_registro_b <> 'POR_REPORTE')
          AND (fecha_inicio IS NULL OR fecha_inicio <= (v_periodo + INTERVAL '1 month' - INTERVAL '1 day')::date)
          AND (fecha_fin IS NULL OR fecha_fin >= v_periodo);

        IF v_otros_candidatos IS NOT NULL THEN
            INSERT INTO validacion_reporte
                (reporte_asistencia_id, trabajador_en_reporte_id, fecha, codigo_regla, severidad, mensaje, evidencia)
            SELECT ter.reporte_asistencia_id, p_trabajador_en_reporte_id, NULL,
                   'VINCULO_AMBIGUO_MULTIPLE', 'ADVERTENCIA',
                   format('El trabajador tiene %s vinculo(s) mas en esta institucion ademas del elegido (%s); revisar si la marca de asistencia corresponde a otro.',
                          array_length(v_otros_candidatos, 1), v_vinculo_en_b),
                   jsonb_build_object('vinculo_elegido_id', v_vinculo_en_b, 'vinculos_no_elegidos_ids', to_jsonb(v_otros_candidatos))
            FROM trabajador_en_reporte ter WHERE ter.trabajador_en_reporte_id = p_trabajador_en_reporte_id;
        END IF;

        IF v_tipo_registro_b = 'POR_REPORTE' THEN
            INSERT INTO vinculo_trabajador_ie_confirmacion
                (vinculo_trabajador_ie_id, accion, precision_fecha, periodo_confirmado,
                 trabajador_en_reporte_id, motivo, origen, confirmado_por)
            VALUES
                (v_vinculo_en_b, 'CONFIRMA_INICIO', 'PERIODO_MENSUAL', date_trunc('month', v_periodo)::date,
                 p_trabajador_en_reporte_id,
                 format('Reafirmado por el reporte de %s: el trabajador sigue en esta institucion.',
                        to_char(v_periodo, 'YYYY-MM')),
                 'AUTOMATICO_IMPORTACION', NULL)
            ON CONFLICT DO NOTHING;
        END IF;
        RETURN v_vinculo_en_b;
    END IF;

    -- Caso 2: no hay vinculo en la institucion del reporte. ¿NEXUS lo ubica en otra?
    -- Mismo filtro de fecha_inicio que el Caso 1 (ver comentario ahi).
    SELECT vinculo_trabajador_ie_id, institucion_educativa_id INTO v_vinculo_nexus_otra, v_ie_anterior
    FROM vinculo_trabajador_ie
    WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id <> v_reporte_ie
      AND tipo_registro <> 'POR_REPORTE'
      AND (fecha_inicio IS NULL OR fecha_inicio <= (v_periodo + INTERVAL '1 month' - INTERVAL '1 day')::date)
      AND (fecha_fin IS NULL OR fecha_fin >= v_periodo)
    ORDER BY vinculo_trabajador_ie_id LIMIT 1;

    IF v_vinculo_nexus_otra IS NOT NULL THEN
        INSERT INTO vinculo_trabajador_ie
            (trabajador_id, plaza_id, institucion_educativa_id, rol_laboral_id, situacion_laboral, tipo_registro, estado_raw)
        SELECT v_trabajador_id, NULL, v_reporte_ie, COALESCE(v_rol_id, rol_laboral_id),
               situacion_laboral, 'POR_REPORTE', 'Institucion actualizada automaticamente desde reporte de asistencia'
        FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id = v_vinculo_nexus_otra
        ON CONFLICT (trabajador_id, institucion_educativa_id) WHERE tipo_registro = 'POR_REPORTE' AND fecha_fin IS NULL
        DO NOTHING
        RETURNING vinculo_trabajador_ie_id INTO v_nuevo_vinculo;

        IF v_nuevo_vinculo IS NULL THEN
            -- otra transaccion concurrente ya creo este mismo vinculo (uq_vinculo_por_reporte_activo,
            -- race condition real demostrada en la revision SQL 2026-09-10) -- reafirmar, no duplicar.
            SELECT vinculo_trabajador_ie_id INTO v_nuevo_vinculo
            FROM vinculo_trabajador_ie
            WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id = v_reporte_ie
              AND tipo_registro = 'POR_REPORTE' AND fecha_fin IS NULL;

            INSERT INTO vinculo_trabajador_ie_confirmacion
                (vinculo_trabajador_ie_id, accion, precision_fecha, periodo_confirmado,
                 trabajador_en_reporte_id, motivo, origen, confirmado_por)
            VALUES
                (v_nuevo_vinculo, 'CONFIRMA_INICIO', 'PERIODO_MENSUAL', date_trunc('month', v_periodo)::date,
                 p_trabajador_en_reporte_id,
                 format('Reafirmado por el reporte de %s: vinculo ya creado por una importacion concurrente.',
                        to_char(v_periodo, 'YYYY-MM')),
                 'AUTOMATICO_IMPORTACION', NULL)
            ON CONFLICT DO NOTHING;

            RETURN v_nuevo_vinculo;
        END IF;

        INSERT INTO vinculo_trabajador_ie_confirmacion
            (vinculo_trabajador_ie_id, accion, precision_fecha, periodo_confirmado,
             trabajador_en_reporte_id, vinculo_trabajador_ie_relacionado_id, motivo, origen, confirmado_por)
        VALUES
            (v_nuevo_vinculo, 'CONFIRMA_INICIO', 'PERIODO_MENSUAL', date_trunc('month', v_periodo)::date,
             p_trabajador_en_reporte_id, v_vinculo_nexus_otra,
             format('NEXUS ubica al trabajador en la institucion %s; el reporte de %s lo declara en la institucion %s. Actualizacion automatica al importar.',
                    v_ie_anterior, to_char(v_periodo, 'YYYY-MM'), v_reporte_ie),
             'AUTOMATICO_IMPORTACION', NULL);

        INSERT INTO validacion_reporte
            (reporte_asistencia_id, trabajador_en_reporte_id, fecha, codigo_regla, severidad, mensaje, evidencia)
        SELECT ter.reporte_asistencia_id, p_trabajador_en_reporte_id, NULL,
               'INSTITUCION_ACTUALIZADA_AUTOMATICAMENTE', 'ADVERTENCIA',
               format('Institucion actualizada por defecto de %s a %s para %s; revisar si corresponde.',
                      v_ie_anterior, v_reporte_ie, to_char(v_periodo, 'YYYY-MM')),
               jsonb_build_object(
                   'vinculo_anterior_id', v_vinculo_nexus_otra, 'vinculo_nuevo_id', v_nuevo_vinculo,
                   'institucion_anterior_id', v_ie_anterior, 'institucion_nueva_id', v_reporte_ie)
        FROM trabajador_en_reporte ter WHERE ter.trabajador_en_reporte_id = p_trabajador_en_reporte_id;

        RETURN v_nuevo_vinculo;
    END IF;

    -- Caso 3: NEXUS no conoce a este trabajador en ninguna institucion (alta nueva, o plaza VACANTE
    -- que NEXUS todavia no actualizo). Confirmado por RRHH el 2026-09-10 (decision de vigencia §14.1):
    -- se crea el vinculo automaticamente igual que el Caso 2, con su propio codigo_regla para que la
    -- revision pueda distinguirlo de una reasignacion de institucion conocida.
    -- La situacion_laboral no se inventa: se toma de la condicion que el propio reporte declaro para
    -- esta persona (columna "Condicion" del ANEXO 3/4). Sin esa condicion reconocible, o sin rol
    -- resuelto, no se crea nada -- queda pendiente, igual que en los demas casos de esta funcion.
    SELECT UPPER(TRIM(condicion_reportada_raw)) INTO v_situacion_reportada
    FROM trabajador_en_reporte WHERE trabajador_en_reporte_id = p_trabajador_en_reporte_id;

    IF v_rol_id IS NULL
       OR v_situacion_reportada IS NULL
       OR v_situacion_reportada NOT IN ('NOMBRADO', 'CONTRATADO', 'DESIGNADO', 'ENCARGADO', 'DESTACADO') THEN
        RETURN NULL;
    END IF;

    INSERT INTO vinculo_trabajador_ie
        (trabajador_id, plaza_id, institucion_educativa_id, rol_laboral_id, situacion_laboral, tipo_registro, estado_raw)
    VALUES
        (v_trabajador_id, NULL, v_reporte_ie, v_rol_id, v_situacion_reportada, 'POR_REPORTE',
         'Vinculo creado automaticamente: trabajador no encontrado en ningun corte NEXUS')
    ON CONFLICT (trabajador_id, institucion_educativa_id) WHERE tipo_registro = 'POR_REPORTE' AND fecha_fin IS NULL
    DO NOTHING
    RETURNING vinculo_trabajador_ie_id INTO v_nuevo_vinculo;

    IF v_nuevo_vinculo IS NULL THEN
        -- otra transaccion concurrente ya creo este mismo vinculo (uq_vinculo_por_reporte_activo,
        -- misma race condition del Caso 2, ver comentario ahi) -- reafirmar, no duplicar.
        SELECT vinculo_trabajador_ie_id INTO v_nuevo_vinculo
        FROM vinculo_trabajador_ie
        WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id = v_reporte_ie
          AND tipo_registro = 'POR_REPORTE' AND fecha_fin IS NULL;

        INSERT INTO vinculo_trabajador_ie_confirmacion
            (vinculo_trabajador_ie_id, accion, precision_fecha, periodo_confirmado,
             trabajador_en_reporte_id, motivo, origen, confirmado_por)
        VALUES
            (v_nuevo_vinculo, 'CONFIRMA_INICIO', 'PERIODO_MENSUAL', date_trunc('month', v_periodo)::date,
             p_trabajador_en_reporte_id,
             format('Reafirmado por el reporte de %s: vinculo ya creado por una importacion concurrente.',
                    to_char(v_periodo, 'YYYY-MM')),
             'AUTOMATICO_IMPORTACION', NULL)
        ON CONFLICT DO NOTHING;

        RETURN v_nuevo_vinculo;
    END IF;

    INSERT INTO vinculo_trabajador_ie_confirmacion
        (vinculo_trabajador_ie_id, accion, precision_fecha, periodo_confirmado,
         trabajador_en_reporte_id, motivo, origen, confirmado_por)
    VALUES
        (v_nuevo_vinculo, 'CONFIRMA_INICIO', 'PERIODO_MENSUAL', date_trunc('month', v_periodo)::date,
         p_trabajador_en_reporte_id,
         format('Trabajador no encontrado en ningun corte NEXUS; institucion asignada automaticamente desde el reporte de %s.',
                to_char(v_periodo, 'YYYY-MM')),
         'AUTOMATICO_IMPORTACION', NULL);

    INSERT INTO validacion_reporte
        (reporte_asistencia_id, trabajador_en_reporte_id, fecha, codigo_regla, severidad, mensaje, evidencia)
    SELECT ter.reporte_asistencia_id, p_trabajador_en_reporte_id, NULL,
           'TRABAJADOR_SIN_NEXUS_VINCULADO_AUTOMATICAMENTE', 'ADVERTENCIA',
           format('Trabajador vinculado automaticamente a %s para %s; no existe en ningun corte NEXUS. Revisar si corresponde.',
                  v_reporte_ie, to_char(v_periodo, 'YYYY-MM')),
           jsonb_build_object('vinculo_nuevo_id', v_nuevo_vinculo, 'institucion_id', v_reporte_ie)
    FROM trabajador_en_reporte ter WHERE ter.trabajador_en_reporte_id = p_trabajador_en_reporte_id;

    RETURN v_nuevo_vinculo;
END;
$$ LANGUAGE plpgsql;
