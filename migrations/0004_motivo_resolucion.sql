-- Motivo de resolucion de una alerta (validacion_reporte).
-- Origen: docs/ux/REVISION_PRE_P06.md §2.2 -- el prototipo de P06-UX ya muestra y "conserva" un
-- motivo al registrar una decision de RRHH sobre una alerta, pero el esquema de P05 solo tenia
-- estado/resuelta_por/resuelta_en: ese motivo se habria perdido en silencio al guardar.
-- Nullable a proposito (no CHECK que lo exija junto a RESUELTA): el escenario 13.f de
-- docs/decisions/2026-09-10-vigencia-vinculo-trabajador-institucion.md ya preve que aceptar una
-- correccion automatica tal cual, sin mas accion, es una resolucion valida sin motivo adicional.

ALTER TABLE validacion_reporte
    ADD COLUMN motivo_resolucion text NULL;
