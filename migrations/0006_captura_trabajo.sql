-- Completa la captura de tarea nivel/mes; no fabrica referencia ni resultado M02/M03/M08.
ALTER TABLE web_tarea ADD COLUMN lote_referencia text NOT NULL DEFAULT '';
ALTER TABLE web_tarea ADD COLUMN version_referencia text NOT NULL DEFAULT '';
ALTER TABLE web_tarea ADD COLUMN criterio_terminacion text NOT NULL DEFAULT '';

CREATE TABLE web_tarea_evento (
    operacion_id uuid PRIMARY KEY,
    web_tarea_id uuid NOT NULL REFERENCES web_tarea(web_tarea_id),
    accion text NOT NULL,
    datos jsonb NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER web_tarea_evento_inmutable BEFORE UPDATE OR DELETE ON web_tarea_evento
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();
