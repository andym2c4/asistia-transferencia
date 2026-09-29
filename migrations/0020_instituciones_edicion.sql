-- RF-F05: historial e idempotencia de mantenimiento institucional y complemento del padrón.
-- No reinterpreta ni reemplaza registros anteriores; no contiene datos del padrón real.
CREATE TABLE institucion_cambio (
    operacion_id uuid PRIMARY KEY,
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa,
    accion text NOT NULL CHECK (accion IN ('CREAR','EDITAR','COMPLETAR_PADRON')),
    solicitud jsonb NOT NULL,
    valores_anteriores jsonb,
    valores_nuevos jsonb NOT NULL,
    autor bigint REFERENCES usuario,
    responsable text NOT NULL CHECK (length(trim(responsable)) > 0),
    motivo text NOT NULL CHECK (length(trim(motivo)) > 0),
    fuente jsonb NOT NULL DEFAULT '{}',
    creado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (accion = 'COMPLETAR_PADRON' OR autor IS NOT NULL)
);
CREATE INDEX ix_institucion_cambio_historial ON institucion_cambio(institucion_educativa_id,creado_en DESC);
CREATE TRIGGER institucion_cambio_inmutable BEFORE UPDATE OR DELETE ON institucion_cambio
FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();
