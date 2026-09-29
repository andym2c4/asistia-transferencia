-- Categoría compartida != código local. El significado recibido permanece en cada documento.
CREATE TABLE leyenda_categoria (
    categoria_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dominio text NOT NULL CHECK (dominio IN ('asistencia','calendario')),
    nombre text NOT NULL CHECK (length(trim(nombre)) BETWEEN 1 AND 500),
    clave text NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE(dominio,clave), UNIQUE(categoria_id,dominio)
);
CREATE TABLE leyenda_categoria_version (
    categoria_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    categoria_id bigint NOT NULL REFERENCES leyenda_categoria,
    version integer NOT NULL CHECK(version>0),
    es_remunerado boolean,
    grupo_actividad text CHECK(grupo_actividad IN ('LECTIVO','GESTION','NO_LECTIVO_NI_GESTION')),
    es_falta boolean,
    motivo text NOT NULL CHECK(length(trim(motivo))>0),
    autor bigint REFERENCES usuario,
    creado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE(categoria_id,version)
);
CREATE TABLE leyenda_equivalencia (
    dominio text NOT NULL,
    significado text NOT NULL,
    descripcion text NOT NULL,
    categoria_id bigint NOT NULL,
    PRIMARY KEY(dominio,significado),
    FOREIGN KEY(categoria_id,dominio) REFERENCES leyenda_categoria(categoria_id,dominio)
);
CREATE TABLE leyenda_operacion (
    operacion_id uuid PRIMARY KEY,
    accion text NOT NULL,
    solicitud jsonb NOT NULL,
    resultado jsonb NOT NULL,
    autor bigint NOT NULL REFERENCES usuario,
    motivo text NOT NULL CHECK(length(trim(motivo))>0),
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER leyenda_version_inmutable BEFORE UPDATE OR DELETE ON leyenda_categoria_version
FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();
CREATE TRIGGER leyenda_operacion_inmutable BEFORE UPDATE OR DELETE ON leyenda_operacion
FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();

-- Nombres iniciales de uso común; no son reglas de pago ni equivalencias aprobadas.
INSERT INTO leyenda_categoria(dominio,nombre,clave) VALUES
('asistencia','Asistencia','ASISTENCIA'),
('asistencia','Licencia con goce','LICENCIA CON GOCE'),
('asistencia','Licencia sin goce','LICENCIA SIN GOCE'),
('asistencia','Permiso con goce','PERMISO CON GOCE'),
('asistencia','Permiso sin goce','PERMISO SIN GOCE'),
('asistencia','Inasistencia justificada','INASISTENCIA JUSTIFICADA'),
('asistencia','Inasistencia injustificada','INASISTENCIA INJUSTIFICADA'),
('asistencia','Tardanza','TARDANZA'),
('asistencia','Feriado','FERIADO'),
('asistencia','Comisión de servicio','COMISION DE SERVICIO'),
('asistencia','Capacitación','CAPACITACION'),
('asistencia','Huelga o paro','HUELGA O PARO'),
('calendario','Día lectivo','DIA LECTIVO'),
('calendario','Gestión','GESTION'),
('calendario','Feriado','FERIADO'),
('calendario','Sábado','SABADO'),
('calendario','Domingo','DOMINGO'),
('calendario','Vacaciones','VACACIONES');
INSERT INTO leyenda_categoria_version(categoria_id,version,motivo)
SELECT categoria_id,1,'Catálogo inicial. Regla pendiente de definir con sustento.' FROM leyenda_categoria;
