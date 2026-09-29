-- Cierre formal: progreso recuperable y procedencia explícita; sin aprobación ficticia.
CREATE TABLE cierre_documento (
    cierre_documento_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256 char(64) NOT NULL,
    tipo text NOT NULL CHECK (tipo IN ('NEXUS','DRE','CALENDARIO_2026','CALENDARIO_2025','ASISTENCIA')),
    ruta text NOT NULL,
    rutas jsonb NOT NULL DEFAULT '[]',
    documento_recibido_id uuid REFERENCES documento_recibido,
    estado text NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN
      ('PENDIENTE','PROCESANDO','PROCESADO','PARCIAL','ERROR','ESPERANDO_CUPO','SIN_DATOS','NO_NECESARIO')),
    metodo text,
    intentos integer NOT NULL DEFAULT 0,
    resultado jsonb NOT NULL DEFAULT '{}',
    error text,
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sha256,tipo)
);
CREATE TABLE padron_evidencia (
    padron_evidencia_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cierre_documento_id uuid NOT NULL REFERENCES cierre_documento,
    institucion_educativa_id bigint REFERENCES institucion_educativa,
    trabajador_id bigint REFERENCES trabajador,
    periodo date NOT NULL,
    hoja text NOT NULL,
    fila integer NOT NULL,
    institucion_raw text,
    nombres_raw text NOT NULL,
    cargo_raw text,
    datos_raw jsonb NOT NULL,
    metodo_identidad text NOT NULL DEFAULT 'PENDIENTE',
    UNIQUE (cierre_documento_id,hoja,fila)
);
CREATE INDEX ix_padron_evidencia_ie ON padron_evidencia(institucion_educativa_id,periodo);
ALTER TABLE calendarizacion_version ADD COLUMN procedencia_extraccion jsonb NOT NULL DEFAULT '{}';
ALTER TABLE reporte_asistencia ADD COLUMN procedencia_extraccion jsonb NOT NULL DEFAULT '{}';
CREATE TABLE cierre_revision_institucion (
    cierre_revision_institucion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa,
    periodo date NOT NULL,
    huella text NOT NULL,
    autor text NOT NULL DEFAULT 'AGENTE_TECNICO',
    resultado text NOT NULL,
    detalle jsonb NOT NULL,
    revisado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institucion_educativa_id,periodo,huella)
);
ALTER TABLE nexus_carga ADD COLUMN filas_registradas integer NOT NULL DEFAULT 0;
ALTER TABLE nexus_carga ADD COLUMN estado_integridad text NOT NULL DEFAULT 'SIN_VERIFICAR'
 CHECK (estado_integridad IN ('SIN_VERIFICAR','COMPLETA','PARCIAL','FALLIDA'));
UPDATE nexus_carga c SET filas_registradas=(SELECT count(*) FROM nexus_registro r WHERE r.nexus_carga_id=c.nexus_carga_id);
UPDATE nexus_carga SET estado_integridad=CASE WHEN filas_registradas=total_filas THEN 'COMPLETA'
 WHEN filas_registradas=0 THEN 'FALLIDA' ELSE 'PARCIAL' END;
