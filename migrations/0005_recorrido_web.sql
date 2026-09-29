-- P06: acceso, recepción real, ejecución recuperable, revisión local y salidas conservadas.
-- Aditiva: no cambia los estados BORRADOR/ENVIADO ni reescribe fuentes anteriores.

CREATE FUNCTION fn_nivel_canonico(valor text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE upper(regexp_replace(btrim(translate(valor, 'áéíóúÁÉÍÓÚ', 'aeiouAEIOU')), '\s+', ' ', 'g'))
        WHEN 'INICIAL' THEN 'INICIAL'
        WHEN 'INICIAL - JARDIN' THEN 'INICIAL'
        WHEN 'INICIAL - PROGRAMA NO ESCOLARIZADO' THEN 'INICIAL'
        WHEN 'PRIMARIA' THEN 'PRIMARIA'
        WHEN 'SECUNDARIA' THEN 'SECUNDARIA'
        WHEN 'CEBA' THEN 'CEBA'
        WHEN 'BASICA ALTERNATIVA-AVANZADO' THEN 'CEBA'
        WHEN 'BASICA ALTERNATIVA-INICIAL E INTERMEDIO' THEN 'CEBA'
        WHEN 'CEBE' THEN 'CEBE'
        WHEN 'BASICA ESPECIAL-INICIAL' THEN 'CEBE'
        WHEN 'BASICA ESPECIAL-PRIMARIA' THEN 'CEBE'
        WHEN 'PRITE' THEN 'PRITE'
        WHEN 'CETPRO' THEN 'CETPRO'
        WHEN 'TECNICO PRODUCTIVA' THEN 'CETPRO'
        ELSE NULL END
$$;

CREATE TABLE web_credencial (
    usuario_id bigint PRIMARY KEY REFERENCES usuario(usuario_id),
    password_hash text NOT NULL,
    fallos integer NOT NULL DEFAULT 0 CHECK (fallos >= 0),
    bloqueado_hasta timestamptz NULL,
    actualizado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE web_sesion (
    token_hash char(64) PRIMARY KEY,
    usuario_id bigint NOT NULL REFERENCES usuario(usuario_id),
    vence_en timestamptz NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE web_carga (
    web_carga_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    objeto_archivo_id uuid NOT NULL REFERENCES objeto_archivo(objeto_archivo_id),
    ruta_copia text NOT NULL,
    tipo text NOT NULL CHECK (tipo IN ('nexus', 'calendario', 'asistencia')),
    fecha_corte date NULL,
    estado text NOT NULL DEFAULT 'RECIBIDO' CHECK
        (estado IN ('RECIBIDO', 'PROCESANDO', 'PROCESADO', 'PARCIAL', 'ERROR', 'NO_SOPORTADO')),
    resultado jsonb NOT NULL DEFAULT '{}'::jsonb,
    mensaje text NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (objeto_archivo_id, tipo, fecha_corte),
    CHECK ((tipo = 'nexus') = (fecha_corte IS NOT NULL))
);
CREATE TABLE web_recepcion (
    web_recepcion_id uuid PRIMARY KEY,
    web_carga_id uuid NOT NULL REFERENCES web_carga(web_carga_id),
    nombre_original text NOT NULL,
    recibido_real_en timestamptz NULL,
    periodo_esperado date NULL,
    cargado_por bigint NOT NULL REFERENCES usuario(usuario_id),
    cargado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (periodo_esperado IS NULL OR periodo_esperado = date_trunc('month', periodo_esperado)::date)
);
CREATE TRIGGER web_recepcion_inmutable BEFORE UPDATE OR DELETE ON web_recepcion
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();
CREATE TABLE web_carga_intento (
    web_carga_intento_id uuid PRIMARY KEY,
    web_carga_id uuid NOT NULL REFERENCES web_carga(web_carga_id),
    iniciado_por bigint NOT NULL REFERENCES usuario(usuario_id),
    iniciado_en timestamptz NOT NULL DEFAULT now(),
    terminado_en timestamptz NULL,
    estado text NOT NULL DEFAULT 'PROCESANDO',
    resultado jsonb NOT NULL DEFAULT '{}'::jsonb,
    mensaje text NULL,
    CHECK (estado IN ('PROCESANDO', 'PROCESADO', 'PARCIAL', 'ERROR', 'INTERRUMPIDO', 'NO_SOPORTADO'))
);
CREATE INDEX web_carga_intento_carga ON web_carga_intento(web_carga_id, iniciado_en);

-- Una fuente sin personas identificadas también pertenece al ámbito del consolidado.
CREATE TABLE consolidado_reporte_fuente (
    consolidado_dre_id uuid NOT NULL REFERENCES consolidado_dre(consolidado_dre_id),
    reporte_asistencia_id uuid NOT NULL REFERENCES reporte_asistencia(reporte_asistencia_id),
    PRIMARY KEY (consolidado_dre_id, reporte_asistencia_id)
);
INSERT INTO consolidado_reporte_fuente
SELECT DISTINCT d.consolidado_dre_id, f.reporte_asistencia_id
FROM consolidado_dre_detalle d
JOIN consolidado_dre_detalle_fuente f USING (consolidado_dre_detalle_id)
WHERE f.reporte_asistencia_id IS NOT NULL;

CREATE TABLE web_revision_evento (
    web_revision_evento_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operacion_id uuid NOT NULL UNIQUE,
    reporte_asistencia_id uuid NOT NULL REFERENCES reporte_asistencia(reporte_asistencia_id),
    validacion_reporte_id uuid NULL REFERENCES validacion_reporte(validacion_reporte_id),
    trabajador_en_reporte_id uuid NULL REFERENCES trabajador_en_reporte(trabajador_en_reporte_id),
    accion text NOT NULL,
    motivo text NOT NULL CHECK (length(btrim(motivo)) > 0),
    anterior jsonb NOT NULL,
    posterior jsonb NOT NULL,
    usuario_id bigint NOT NULL REFERENCES usuario(usuario_id),
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER web_revision_evento_inmutable BEFORE UPDATE OR DELETE ON web_revision_evento
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();

CREATE TABLE web_salida (
    web_salida_id uuid PRIMARY KEY,
    consolidado_dre_id uuid NOT NULL UNIQUE REFERENCES consolidado_dre(consolidado_dre_id),
    objeto_archivo_id uuid NOT NULL REFERENCES objeto_archivo(objeto_archivo_id),
    estado_revision text NOT NULL CHECK (estado_revision IN ('BORRADOR', 'REVISADO')),
    manifiesto jsonb NOT NULL,
    creado_por bigint NOT NULL REFERENCES usuario(usuario_id),
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER web_salida_inmutable BEFORE UPDATE OR DELETE ON web_salida
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();

-- Captura de trabajo compartido nivel/mes. Las ejecuciones técnicas son PRACTICA,
-- nunca una medición de RRHH ni un porcentaje de éxito.
CREATE TABLE web_tarea (
    web_tarea_id uuid PRIMARY KEY,
    usuario_id bigint NOT NULL REFERENCES usuario(usuario_id),
    periodo date NOT NULL CHECK (periodo = date_trunc('month', periodo)::date),
    nivel text NOT NULL,
    cohorte text NOT NULL,
    tarea_referencia text NOT NULL,
    uso text NOT NULL CHECK (uso IN ('PRACTICA', 'OBSERVACION')),
    estado text NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA','PAUSADA','FINALIZADA','ABANDONADA')),
    ayuda_tecnica boolean NOT NULL DEFAULT false,
    salida_id uuid NULL REFERENCES web_salida(web_salida_id),
    creado_en timestamptz NOT NULL DEFAULT now(),
    terminado_en timestamptz NULL
);
CREATE UNIQUE INDEX web_una_tarea_abierta ON web_tarea(usuario_id)
    WHERE estado IN ('ACTIVA', 'PAUSADA');
CREATE TABLE web_tarea_intervalo (
    web_tarea_intervalo_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    web_tarea_id uuid NOT NULL REFERENCES web_tarea(web_tarea_id),
    inicio timestamptz NOT NULL DEFAULT now(),
    fin timestamptz NULL,
    CHECK (fin IS NULL OR fin >= inicio)
);
CREATE UNIQUE INDEX web_un_intervalo_abierto ON web_tarea_intervalo(web_tarea_id) WHERE fin IS NULL;
