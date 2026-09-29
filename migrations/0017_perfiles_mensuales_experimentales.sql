-- Dominio experimental: nunca se consume como asistencia diaria ni remuneración.
CREATE TABLE ml_lote (
    ml_lote_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    huella char(64) NOT NULL UNIQUE,
    naturaleza text NOT NULL DEFAULT 'SINTETICO_DERIVADO_DRE'
      CHECK (naturaleza = 'SINTETICO_DERIVADO_DRE'),
    padre_id uuid REFERENCES ml_lote,
    configuracion jsonb NOT NULL,
    manifiesto jsonb NOT NULL,
    autor text NOT NULL,
    motivo text NOT NULL CHECK (length(trim(motivo)) > 0),
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ml_reporte_mensual (
    ml_reporte_mensual_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ml_lote_id uuid NOT NULL REFERENCES ml_lote,
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa,
    periodo_fuente date NOT NULL CHECK (extract(day from periodo_fuente) = 1),
    periodo date NOT NULL CHECK (extract(day from periodo) = 1),
    datos jsonb NOT NULL,
    fuentes jsonb NOT NULL,
    UNIQUE (ml_lote_id,institucion_educativa_id,periodo),
    UNIQUE (ml_lote_id,institucion_educativa_id,periodo_fuente)
);
CREATE TABLE ml_experimento (
    ml_experimento_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ml_lote_id uuid NOT NULL REFERENCES ml_lote,
    huella char(64) NOT NULL UNIQUE,
    manifiesto jsonb NOT NULL,
    modelo bytea NOT NULL,
    paquete bytea,
    resultados jsonb NOT NULL,
    autor text NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now()
);
