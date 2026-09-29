-- Resoluciones del documento conservadas con motivo e historial, sin aprobación implícita.
ALTER TABLE cierre_documento ADD COLUMN contexto_tecnico jsonb NOT NULL DEFAULT '{}';
