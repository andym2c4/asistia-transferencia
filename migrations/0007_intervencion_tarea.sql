-- Vincular solo decisiones nuevas a la tarea activa del operador y del mismo ámbito.
-- No atribuir retroactivamente historia ni registros hechos durante una pausa.
ALTER TABLE web_revision_evento ADD COLUMN web_tarea_id uuid NULL REFERENCES web_tarea(web_tarea_id);
CREATE INDEX web_revision_tarea ON web_revision_evento(web_tarea_id) WHERE web_tarea_id IS NOT NULL;
