-- Cortes analíticos independientes: no modifican asistencia, pagos ni modelos DRE.
CREATE TABLE monitoreo_diario_corte (
    corte_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    huella char(64) NOT NULL UNIQUE,
    anio integer NOT NULL CHECK (anio BETWEEN 2000 AND 2100),
    naturaleza text NOT NULL DEFAULT 'DERIVADO_DOCUMENTOS_DIARIOS'
      CHECK (naturaleza = 'DERIVADO_DOCUMENTOS_DIARIOS'),
    manifiesto jsonb NOT NULL,
    modelo bytea,
    paquete bytea,
    resultados jsonb NOT NULL,
    autor text NOT NULL,
    motivo text NOT NULL CHECK (length(trim(motivo)) BETWEEN 5 AND 2000),
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE monitoreo_diario_perfil (
    perfil_id uuid PRIMARY KEY,
    corte_id uuid NOT NULL REFERENCES monitoreo_diario_corte,
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa,
    periodo date NOT NULL CHECK (extract(day from periodo) = 1),
    identidad jsonb NOT NULL,
    datos jsonb NOT NULL,
    fuentes jsonb NOT NULL,
    UNIQUE (corte_id, institucion_educativa_id, periodo)
);
