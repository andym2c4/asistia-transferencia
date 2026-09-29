-- Esquema base de ASISTIA (v2), acotado al importador de NEXUS/calendario/asistencia Excel.
--
-- Origen: subconjunto de data/MODELO_DATOS_UGEL_LUYA.md (esquema real de v1, ya verificado contra
-- Postgres 16), con dos correcciones aplicadas desde el diseño en vez de como parche posterior:
--
--   1. ADR-014 de v1 (calendarizacion_local.md): la calendarizacion se versiona por
--      institucion_educativa_id, no por local_educativo_id -- un mismo local puede tener
--      Primaria/Secundaria con calendarios distintos (caso real: Tactamal, El Yeso). El nombre de
--      tabla se conserva (mismo razonamiento del ADR: renombrar es puro churn de identificador).
--   2. ADR-019 de v1: asistencia_dia.estado_captura incluye 'DERIVADO' desde el inicio (marca dias
--      derivados del ANEXO 4 o de la calendarizacion, distinguibles de lo que un director escribio).
--
-- Explicitamente fuera de este esquema (ver docs/exec-plans o el plan de arranque en
-- /home/sprinkle/.claude/plans/steady-sniffing-frost.md): extraccion documental/OCR/vision,
-- deteccion de fraude, plantilla_calendario_* (v1 las declaro muertas en su propio TECH_DEBT.md),
-- padron_importacion* (no se construye un importador de padron en este primer corte),
-- periodo_academico y solicitud_correccion_calendarizacion (flujo de correccion, no de importacion).
--
-- Alcance de integridad de este primer corte: se incluyen las restricciones que evitan datos
-- semanticamente incorrectos (EXCLUDE, CHECK, coherencia entre tablas, auditoria append-only). Se
-- omiten deliberadamente los triggers de "freeze" mas elaborados sobre transiciones de estado
-- (p. ej. bloquear edicion de un reporte ya VALIDADO) porque este primer entregable es un pipeline
-- de importacion de una sola pasada, no una UI de edicion -- se agregan cuando exista esa UI.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ============================================================
-- 0. Usuario, archivos, catalogos
-- ============================================================

CREATE TABLE usuario (
    usuario_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre varchar(150) NOT NULL,
    email varchar(150) NOT NULL UNIQUE,
    rol varchar(30) NOT NULL DEFAULT 'RRHH',
    activo boolean NOT NULL DEFAULT true,
    creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE objeto_archivo (
    objeto_archivo_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256 char(64) NOT NULL UNIQUE,
    ruta_objeto text NOT NULL,
    mime_type text NOT NULL,
    tamano_bytes bigint NOT NULL CHECK (tamano_bytes >= 0),
    primera_vez_visto_en timestamptz NOT NULL DEFAULT now(),
    CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE documento_recibido (
    documento_recibido_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    objeto_archivo_id uuid NOT NULL REFERENCES objeto_archivo(objeto_archivo_id),
    nombre_original text NOT NULL,
    recibido_en timestamptz NOT NULL DEFAULT now(),
    cargado_por bigint NULL REFERENCES usuario(usuario_id)
);
CREATE INDEX ix_documento_recibido_objeto ON documento_recibido (objeto_archivo_id);

CREATE TABLE catalogo_rol_laboral (
    rol_laboral_id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo varchar(30) NOT NULL UNIQUE,
    nombre varchar(120) NOT NULL,
    codigo_oficial varchar(30) NULL UNIQUE,
    activo boolean NOT NULL DEFAULT true
);

CREATE TABLE catalogo_estado_asistencia (
    estado_asistencia_id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo varchar(30) NOT NULL UNIQUE,
    nombre varchar(120) NOT NULL,
    categoria varchar(30) NOT NULL,
    codigo_oficial varchar(30) NULL UNIQUE,
    impacta_descuento boolean NULL,
    activo boolean NOT NULL DEFAULT true,
    vigente_desde date NULL,
    vigente_hasta date NULL,
    CHECK (vigente_hasta IS NULL OR vigente_desde IS NULL OR vigente_hasta >= vigente_desde)
);

CREATE TABLE catalogo_tipo_dia (
    tipo_dia_id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo_interno varchar(40) NOT NULL UNIQUE,
    nombre varchar(120) NOT NULL,
    codigo_oficial varchar(40) NULL UNIQUE,
    activo boolean NOT NULL DEFAULT true,
    CHECK (codigo_interno IN ('LECTIVO', 'GESTION', 'NO_LECTIVO_NI_GESTION'))
);

CREATE TABLE catalogo_codigo_tipo_dia_fuente (
    catalogo_codigo_tipo_dia_fuente_id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    familia_formato varchar(40) NOT NULL,
    codigo_raw varchar(20) NOT NULL,
    tipo_dia_id smallint NOT NULL REFERENCES catalogo_tipo_dia(tipo_dia_id),
    vigente_desde date NULL,
    vigente_hasta date NULL,
    CHECK (vigente_hasta IS NULL OR vigente_desde IS NULL OR vigente_hasta >= vigente_desde)
);
CREATE UNIQUE INDEX uq_codigo_tipo_dia_fuente_activo
    ON catalogo_codigo_tipo_dia_fuente (familia_formato, codigo_raw)
    WHERE vigente_hasta IS NULL;

CREATE TABLE parametro_minimo_dias_calendario (
    anio smallint NOT NULL,
    tipo_dia_id smallint NOT NULL REFERENCES catalogo_tipo_dia(tipo_dia_id),
    dias_minimos integer NOT NULL CHECK (dias_minimos >= 0),
    PRIMARY KEY (anio, tipo_dia_id)
);
-- Vacia a proposito en este corte: sin filas, el minimo no se exige (ver
-- fn_calendarizacion_version_check_completa). Se puebla cuando la UGEL confirme minimos oficiales.

CREATE TABLE validacion_calendarizacion (
    validacion_calendarizacion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    calendarizacion_version_id uuid NOT NULL,
    fecha date NULL,
    codigo_regla varchar(60) NOT NULL,
    severidad varchar(20) NOT NULL,
    mensaje text NOT NULL,
    evidencia jsonb NOT NULL DEFAULT '{}'::jsonb,
    estado varchar(20) NOT NULL DEFAULT 'PENDIENTE',
    resuelta_por bigint NULL REFERENCES usuario(usuario_id),
    resuelta_en timestamptz NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (severidad IN ('INFORMATIVA', 'ADVERTENCIA', 'ERROR')),
    CHECK (estado IN ('PENDIENTE', 'RESUELTA', 'DESCARTADA')),
    CHECK (estado = 'PENDIENTE' OR (resuelta_por IS NOT NULL AND resuelta_en IS NOT NULL))
);
-- FK a calendarizacion_version se agrega mas abajo (ALTER) porque esa tabla aun no existe aqui.

-- ============================================================
-- 1. Local educativo, institucion educativa
-- ============================================================

CREATE TABLE local_educativo (
    local_educativo_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codlocal_escale varchar(20) NULL,
    nombre_local text NULL,
    direccion text NULL,
    centro_poblado text NULL,
    localidad text NULL,
    distrito varchar(60) NULL,
    provincia varchar(60) NULL,
    departamento varchar(60) NULL,
    ubigeo char(6) NULL,
    area_censal varchar(20) NULL,
    latitud numeric(9,6) NULL,
    longitud numeric(9,6) NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (latitud IS NULL OR latitud BETWEEN -90 AND 90),
    CHECK (longitud IS NULL OR longitud BETWEEN -180 AND 180)
);
CREATE UNIQUE INDEX uq_local_educativo_codlocal_escale
    ON local_educativo (codlocal_escale) WHERE codlocal_escale IS NOT NULL;

CREATE TABLE institucion_educativa (
    institucion_educativa_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    local_educativo_id bigint NOT NULL REFERENCES local_educativo(local_educativo_id),
    cod_mod char(7) NOT NULL,
    anexo varchar(4) NOT NULL DEFAULT '0',
    nombre_ie text NOT NULL,
    nivel_modalidad varchar(80) NOT NULL,
    forma_atencion varchar(30) NOT NULL DEFAULT 'Escolarizada',
    caracteristica_docente varchar(40) NOT NULL DEFAULT 'No aplica',
    tipo_programa varchar(60) NULL,
    gestion varchar(60) NOT NULL DEFAULT 'Publica de gestion directa',
    gestion_dependencia varchar(60) NOT NULL DEFAULT 'Sector Educacion',
    area_censal varchar(20) NOT NULL DEFAULT 'Rural',
    turno varchar(30) NOT NULL DEFAULT 'Manana',
    departamento varchar(60) NOT NULL DEFAULT 'AMAZONAS',
    provincia varchar(60) NOT NULL DEFAULT 'LUYA',
    distrito varchar(60) NOT NULL,
    region varchar(60) NOT NULL DEFAULT 'AMAZONAS',
    ubigeo char(6) NULL,
    codigo_centro_poblado_inei varchar(20) NULL,
    centro_poblado text NULL,
    localidad text NULL,
    direccion text NULL,
    latitud numeric(9,6) NULL,
    longitud numeric(9,6) NULL,
    codinst_escale varchar(20) NULL,
    codlocal_escale varchar(20) NULL,
    director_reportado_raw text NULL,
    telefono text NULL,
    email text NULL,
    estado varchar(30) NOT NULL DEFAULT 'Activo',
    fecha_actualizacion_escale date NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cod_mod, anexo),
    CHECK (cod_mod ~ '^[0-9]{7}$'),
    CHECK (latitud IS NULL OR latitud BETWEEN -90 AND 90),
    CHECK (longitud IS NULL OR longitud BETWEEN -180 AND 180)
);
CREATE INDEX ix_institucion_educativa_distrito ON institucion_educativa (distrito);
CREATE INDEX ix_institucion_educativa_local ON institucion_educativa (local_educativo_id);

-- ============================================================
-- 2. Trabajador, plaza, vinculo, NEXUS, auditoria
--
-- vinculo_trabajador_ie ya incorpora, desde el diseño, lo confirmado por RRHH el 2026-09-10 en
-- docs/decisions/2026-09-10-vigencia-vinculo-trabajador-institucion.md §7-A y §13.3:
--   - plaza_id es opcional (antes NOT NULL) e institucion_educativa_id es explicito y autoritativo.
--   - tipo_registro incluye 'POR_REPORTE' (antes se llamaba, en el borrador de ese documento,
--     'CONFIRMACION_MANUAL' -- se renombro en §13.3 porque el origen automatico/manual ahora vive
--     en vinculo_trabajador_ie_confirmacion.origen, no en tipo_registro).
-- ============================================================

CREATE TABLE plaza (
    plaza_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa(institucion_educativa_id),
    codigo_plaza varchar(20) NOT NULL UNIQUE,
    cargo_raw text NULL,
    especialidad_raw text NULL,
    categoria_remunerativa_raw text NULL,
    escala_raw text NULL,
    jornada_laboral_raw text NULL,
    creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trabajador (
    trabajador_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dni char(8) NOT NULL UNIQUE,
    apellido_paterno varchar(80) NOT NULL,
    apellido_materno varchar(80) NOT NULL,
    nombres varchar(120) NOT NULL,
    fecha_nacimiento date NULL,
    sexo varchar(15) NULL,
    celular varchar(20) NULL,
    email text NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (dni ~ '^[0-9]{8}$')
);

CREATE TABLE trabajador_auditoria (
    trabajador_auditoria_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trabajador_id bigint NOT NULL,
    operacion varchar(10) NOT NULL CHECK (operacion IN ('INSERT', 'UPDATE', 'DELETE')),
    valores_anteriores jsonb NULL,
    valores_nuevos jsonb NULL,
    modificado_por bigint NULL REFERENCES usuario(usuario_id),
    modificado_en timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_trabajador_auditoria_trabajador ON trabajador_auditoria (trabajador_id, modificado_en);

CREATE TABLE vinculo_trabajador_ie (
    vinculo_trabajador_ie_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trabajador_id bigint NULL REFERENCES trabajador(trabajador_id),
    plaza_id bigint NULL REFERENCES plaza(plaza_id),
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa(institucion_educativa_id),
    rol_laboral_id smallint NOT NULL REFERENCES catalogo_rol_laboral(rol_laboral_id),
    sub_tipo_trabajador_raw text NULL,
    cargo_raw text NULL,
    situacion_laboral varchar(30) NOT NULL,
    tipo_registro varchar(20) NOT NULL,
    estado_raw text NULL,
    motivo_vacante_raw text NULL,
    fecha_inicio date NULL,
    fecha_fin date NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (fecha_fin IS NULL OR fecha_inicio IS NULL OR fecha_fin >= fecha_inicio),
    CHECK (situacion_laboral IN ('NOMBRADO','CONTRATADO','DESIGNADO','VACANTE','ENCARGADO','DESTACADO')),
    CHECK (tipo_registro IN ('ORGANICA','POR_REEMPLAZO','REGISTRO_PEC','EVENTUAL','CUADRO_DE_HORAS','POR_REPORTE')),
    CHECK ((situacion_laboral = 'VACANTE') = (trabajador_id IS NULL)),
    CHECK (situacion_laboral <> 'VACANTE' OR plaza_id IS NOT NULL),
    CHECK (plaza_id IS NOT NULL OR tipo_registro = 'POR_REPORTE'),
    EXCLUDE USING gist (
        plaza_id WITH =,
        tipo_registro WITH =,
        daterange(COALESCE(fecha_inicio, '-infinity'::date), COALESCE(fecha_fin, 'infinity'::date), '[]') WITH &&
    )
);
CREATE INDEX ix_vinculo_trabajador_ie_trabajador ON vinculo_trabajador_ie (trabajador_id) WHERE fecha_fin IS NULL;
CREATE INDEX ix_vinculo_trabajador_ie_plaza ON vinculo_trabajador_ie (plaza_id) WHERE fecha_fin IS NULL;
CREATE INDEX ix_vinculo_trabajador_ie_rol ON vinculo_trabajador_ie (rol_laboral_id);
CREATE INDEX ix_vinculo_trabajador_ie_institucion ON vinculo_trabajador_ie (institucion_educativa_id) WHERE fecha_fin IS NULL;
-- No parcial (revision SQL 2026-09-10, medido con EXPLAIN contra datos reales): los Casos 1/2 de
-- fn_resolver_vinculo_por_reporte filtran por (trabajador_id, institucion_educativa_id) admitiendo
-- fecha_fin NULL O poblado ("fecha_fin IS NULL OR fecha_fin >= periodo"), asi que los indices
-- parciales de arriba (WHERE fecha_fin IS NULL) nunca sirven esa consulta -- confirmado: 992 de
-- 1567 filas reales ya tienen fecha_fin poblado, y la consulta caia siempre a Seq Scan.
CREATE INDEX ix_vinculo_trabajador_ie_trabajador_institucion
    ON vinculo_trabajador_ie (trabajador_id, institucion_educativa_id);

CREATE OR REPLACE FUNCTION fn_vinculo_check_institucion() RETURNS trigger AS $$
DECLARE
    ie_plaza bigint;
BEGIN
    IF NEW.plaza_id IS NOT NULL THEN
        SELECT institucion_educativa_id INTO ie_plaza FROM plaza WHERE plaza_id = NEW.plaza_id;
        IF ie_plaza IS DISTINCT FROM NEW.institucion_educativa_id THEN
            RAISE EXCEPTION 'institucion_educativa_id (%) no coincide con la institucion de la plaza % (%)',
                NEW.institucion_educativa_id, NEW.plaza_id, ie_plaza;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_vinculo_check_institucion
    BEFORE INSERT OR UPDATE ON vinculo_trabajador_ie
    FOR EACH ROW EXECUTE FUNCTION fn_vinculo_check_institucion();

CREATE TABLE vinculo_trabajador_ie_auditoria (
    vinculo_trabajador_ie_auditoria_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vinculo_trabajador_ie_id bigint NOT NULL,
    operacion varchar(10) NOT NULL CHECK (operacion IN ('INSERT', 'UPDATE', 'DELETE')),
    valores_anteriores jsonb NULL,
    valores_nuevos jsonb NULL,
    modificado_por bigint NULL REFERENCES usuario(usuario_id),
    modificado_en timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_vinculo_auditoria_vinculo ON vinculo_trabajador_ie_auditoria (vinculo_trabajador_ie_id, modificado_en);

CREATE TABLE nexus_carga (
    nexus_carga_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_recibido_id uuid NOT NULL REFERENCES documento_recibido(documento_recibido_id),
    fecha_corte date NOT NULL,
    estado varchar(20) NOT NULL DEFAULT 'PROCESANDO',
    cargado_por bigint NULL REFERENCES usuario(usuario_id),
    cargado_en timestamptz NOT NULL DEFAULT now(),
    total_filas integer NOT NULL CHECK (total_filas >= 0),
    total_personas_validas integer NOT NULL DEFAULT 0 CHECK (total_personas_validas >= 0),
    total_vacantes integer NOT NULL DEFAULT 0 CHECK (total_vacantes >= 0),
    total_documento_invalido integer NOT NULL DEFAULT 0 CHECK (total_documento_invalido >= 0),
    UNIQUE (documento_recibido_id),
    CHECK (estado IN ('PROCESANDO', 'CERRADA'))
);

CREATE TABLE nexus_registro (
    nexus_registro_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nexus_carga_id uuid NOT NULL REFERENCES nexus_carga(nexus_carga_id),
    fila_origen integer NOT NULL CHECK (fila_origen > 0),
    cod_mod_ie_raw char(7) NOT NULL,
    institucion_educativa_id bigint NULL REFERENCES institucion_educativa(institucion_educativa_id),
    codigo_plaza_raw varchar(20) NOT NULL,
    plaza_id bigint NULL REFERENCES plaza(plaza_id),
    documento_identidad_raw text NOT NULL,
    trabajador_id bigint NULL REFERENCES trabajador(trabajador_id),
    vinculo_trabajador_ie_id bigint NULL REFERENCES vinculo_trabajador_ie(vinculo_trabajador_ie_id),
    tipo_trabajador_raw text NULL,
    sub_tipo_trabajador_raw text NULL,
    cargo_raw text NULL,
    situacion_laboral_raw text NULL,
    estado_raw text NULL,
    tipo_registro_raw text NULL,
    codigo_modular_raw text NULL,
    fecha_inicio_raw text NULL,
    fecha_termino_raw text NULL,
    estado_resolucion varchar(20) NOT NULL DEFAULT 'PENDIENTE',
    creado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (nexus_carga_id, fila_origen),
    CHECK (estado_resolucion IN ('RESUELTO','VACANTE','DOCUMENTO_INVALIDO','PENDIENTE_ANEXO','PENDIENTE')),
    CHECK (estado_resolucion <> 'RESUELTO' OR (
        institucion_educativa_id IS NOT NULL
        AND trabajador_id IS NOT NULL AND vinculo_trabajador_ie_id IS NOT NULL
    ))
);
CREATE INDEX ix_nexus_registro_trabajador ON nexus_registro (trabajador_id);

-- ============================================================
-- 3. Calendarizacion (por institucion educativa -- ADR-014 de v1)
-- ============================================================

CREATE TABLE calendarizacion_local (
    calendarizacion_local_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa(institucion_educativa_id),
    anio smallint NOT NULL CHECK (anio BETWEEN 2020 AND 2100),
    creado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institucion_educativa_id, anio)
);

CREATE TABLE calendarizacion_version (
    calendarizacion_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    calendarizacion_local_id uuid NOT NULL REFERENCES calendarizacion_local(calendarizacion_local_id),
    version smallint NOT NULL CHECK (version > 0),
    version_padre_id uuid NULL REFERENCES calendarizacion_version(calendarizacion_version_id),
    documento_recibido_id uuid NULL REFERENCES documento_recibido(documento_recibido_id),
    origen varchar(30) NOT NULL,
    estado varchar(20) NOT NULL DEFAULT 'BORRADOR',
    motivo_version text NOT NULL,
    total_lectivo_reportado_raw text NULL,
    total_gestion_reportado_raw text NULL,
    total_no_lectivo_reportado_raw text NULL,
    horas_lectivas_reportadas_raw text NULL,
    creado_por bigint NULL REFERENCES usuario(usuario_id),
    creado_en timestamptz NOT NULL DEFAULT now(),
    aprobado_por bigint NULL REFERENCES usuario(usuario_id),
    aprobado_en timestamptz NULL,
    UNIQUE (calendarizacion_local_id, version),
    CHECK (origen IN ('IMPORTACION_IE', 'CORRECCION_UGEL', 'CORRECCION_IE', 'CREACION_MANUAL')),
    CHECK (estado IN ('RECIBIDA', 'BORRADOR', 'EN_REVISION', 'VIGENTE', 'HISTORICA', 'RECHAZADA')),
    CHECK (estado <> 'VIGENTE' OR (aprobado_por IS NOT NULL AND aprobado_en IS NOT NULL))
);
CREATE UNIQUE INDEX uq_calendarizacion_version_vigente
    ON calendarizacion_version (calendarizacion_local_id) WHERE estado = 'VIGENTE';
CREATE INDEX ix_calendarizacion_version_local ON calendarizacion_version (calendarizacion_local_id);
CREATE INDEX ix_calendarizacion_version_padre ON calendarizacion_version (version_padre_id);

ALTER TABLE validacion_calendarizacion
    ADD CONSTRAINT fk_validacion_calendarizacion_version
    FOREIGN KEY (calendarizacion_version_id) REFERENCES calendarizacion_version(calendarizacion_version_id);
-- Vacia a proposito en este corte: la deteccion automatica de discrepancias de calendario
-- (ej. la que confirmamos a mano para secundaria de Tactamal, ver docs/casos/TACTAMAL_JULIO_2026.md
-- §10.2) se implementa cuando exista el motor de reglas; hoy solo existe la tabla para que
-- fn_calendarizacion_version_check_completa pueda consultarla sin fallar.

CREATE TABLE dia_calendarizacion (
    dia_calendarizacion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    calendarizacion_version_id uuid NOT NULL REFERENCES calendarizacion_version(calendarizacion_version_id),
    fecha date NOT NULL,
    tipo_dia_id smallint NULL REFERENCES catalogo_tipo_dia(tipo_dia_id),
    codigo_reportado_raw text NULL,
    estado_captura varchar(20) NOT NULL,
    hoja_origen text NULL,
    celda_origen varchar(20) NULL,
    observacion text NULL,
    confianza numeric(5,4) NULL CHECK (confianza BETWEEN 0 AND 1),
    modelo_extraccion text NULL,
    version_extraccion text NULL,
    UNIQUE (calendarizacion_version_id, fecha),
    CHECK (estado_captura IN ('REGISTRADO', 'VACIO', 'ILEGIBLE', 'CODIGO_DESCONOCIDO', 'NO_APLICA')),
    CHECK ((estado_captura = 'REGISTRADO') = (tipo_dia_id IS NOT NULL))
);
CREATE INDEX ix_dia_calendarizacion_fecha ON dia_calendarizacion (fecha, tipo_dia_id);

CREATE OR REPLACE FUNCTION fn_calendarizacion_version_check_padre() RETURNS trigger AS $$
DECLARE v_padre_local uuid; v_cycle boolean;
BEGIN
    IF NEW.version_padre_id IS NOT NULL THEN
        IF NEW.version_padre_id = NEW.calendarizacion_version_id THEN
            RAISE EXCEPTION 'una version no puede ser su propio version_padre_id';
        END IF;
        SELECT calendarizacion_local_id INTO v_padre_local
        FROM calendarizacion_version WHERE calendarizacion_version_id = NEW.version_padre_id;
        IF v_padre_local IS DISTINCT FROM NEW.calendarizacion_local_id THEN
            RAISE EXCEPTION 'version_padre_id % pertenece a otra calendarizacion_local', NEW.version_padre_id;
        END IF;
        WITH RECURSIVE ascendencia AS (
            SELECT calendarizacion_version_id, version_padre_id FROM calendarizacion_version
            WHERE calendarizacion_version_id = NEW.version_padre_id
            UNION ALL
            SELECT cv.calendarizacion_version_id, cv.version_padre_id
            FROM calendarizacion_version cv JOIN ascendencia a ON cv.calendarizacion_version_id = a.version_padre_id
        )
        SELECT true INTO v_cycle FROM ascendencia WHERE calendarizacion_version_id = NEW.calendarizacion_version_id;
        IF v_cycle THEN
            RAISE EXCEPTION 'version_padre_id % genera un ciclo de versiones', NEW.version_padre_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_calendarizacion_version_check_padre
    BEFORE INSERT OR UPDATE ON calendarizacion_version
    FOR EACH ROW EXECUTE FUNCTION fn_calendarizacion_version_check_padre();

CREATE OR REPLACE FUNCTION fn_calendarizacion_version_check_completa() RETURNS trigger AS $$
DECLARE v_anio smallint; r RECORD;
BEGIN
    IF NEW.estado = 'VIGENTE' AND (OLD IS NULL OR OLD.estado <> 'VIGENTE') THEN
        SELECT anio INTO v_anio FROM calendarizacion_local WHERE calendarizacion_local_id = NEW.calendarizacion_local_id;
        FOR r IN
            SELECT pm.tipo_dia_id, pm.dias_minimos, ct.nombre AS tipo_nombre,
                   (SELECT count(*) FROM dia_calendarizacion dc
                    WHERE dc.calendarizacion_version_id = NEW.calendarizacion_version_id
                      AND dc.estado_captura = 'REGISTRADO' AND dc.tipo_dia_id = pm.tipo_dia_id) AS dias_reales
            FROM parametro_minimo_dias_calendario pm
            JOIN catalogo_tipo_dia ct ON ct.tipo_dia_id = pm.tipo_dia_id
            WHERE pm.anio = v_anio
        LOOP
            IF r.dias_reales < r.dias_minimos THEN
                RAISE EXCEPTION 'version % tiene % dias % REGISTRADO, se exige un minimo de % para el anio %',
                    NEW.calendarizacion_version_id, r.dias_reales, r.tipo_nombre, r.dias_minimos, v_anio;
            END IF;
        END LOOP;
        IF EXISTS (SELECT 1 FROM validacion_calendarizacion
                   WHERE calendarizacion_version_id = NEW.calendarizacion_version_id
                     AND severidad = 'ERROR' AND estado = 'PENDIENTE') THEN
            RAISE EXCEPTION 'version % tiene validaciones ERROR pendientes', NEW.calendarizacion_version_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_calendarizacion_version_check_completa
    BEFORE INSERT OR UPDATE ON calendarizacion_version
    FOR EACH ROW EXECUTE FUNCTION fn_calendarizacion_version_check_completa();

CREATE OR REPLACE FUNCTION fn_calendarizacion_version_auto_demote() RETURNS trigger AS $$
BEGIN
    IF NEW.estado = 'VIGENTE' AND (TG_OP = 'INSERT' OR OLD.estado IS DISTINCT FROM 'VIGENTE') THEN
        UPDATE calendarizacion_version
        SET estado = 'HISTORICA'
        WHERE calendarizacion_local_id = NEW.calendarizacion_local_id
          AND estado = 'VIGENTE'
          AND calendarizacion_version_id <> NEW.calendarizacion_version_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_calendarizacion_version_auto_demote
    BEFORE INSERT OR UPDATE ON calendarizacion_version
    FOR EACH ROW EXECUTE FUNCTION fn_calendarizacion_version_auto_demote();

CREATE OR REPLACE FUNCTION fn_dia_calendarizacion_check_anio() RETURNS trigger AS $$
DECLARE v_anio smallint;
BEGIN
    SELECT cl.anio INTO v_anio FROM calendarizacion_version cv
    JOIN calendarizacion_local cl ON cl.calendarizacion_local_id = cv.calendarizacion_local_id
    WHERE cv.calendarizacion_version_id = NEW.calendarizacion_version_id;
    IF EXTRACT(YEAR FROM NEW.fecha)::smallint <> v_anio THEN
        RAISE EXCEPTION 'fecha % no pertenece al anio % de la calendarizacion', NEW.fecha, v_anio;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_dia_calendarizacion_check_anio
    BEFORE INSERT OR UPDATE ON dia_calendarizacion
    FOR EACH ROW EXECUTE FUNCTION fn_dia_calendarizacion_check_anio();

-- ============================================================
-- 4. Asistencia
-- ============================================================

CREATE TABLE reporte_asistencia_serie (
    reporte_asistencia_serie_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa(institucion_educativa_id),
    periodo date NOT NULL CHECK (periodo = date_trunc('month', periodo)::date),
    nivel_modalidad varchar(80) NOT NULL DEFAULT 'GENERAL',
    turno varchar(40) NOT NULL DEFAULT 'TODOS',
    creado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institucion_educativa_id, periodo, nivel_modalidad, turno)
);
-- Identidad de "serie" de ADR-021 (v1): un reporte distinto para la misma (IE, periodo, nivel,
-- turno) es una version nueva de la misma serie, nunca una fila nueva sin relacion.

CREATE TABLE reporte_asistencia (
    reporte_asistencia_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reporte_asistencia_serie_id uuid NULL REFERENCES reporte_asistencia_serie(reporte_asistencia_serie_id),
    version_padre_id uuid NULL REFERENCES reporte_asistencia(reporte_asistencia_id),
    institucion_educativa_id bigint NULL REFERENCES institucion_educativa(institucion_educativa_id),
    calendarizacion_version_id uuid NULL REFERENCES calendarizacion_version(calendarizacion_version_id),
    documento_recibido_id uuid NOT NULL REFERENCES documento_recibido(documento_recibido_id),
    periodo date NOT NULL CHECK (periodo = date_trunc('month', periodo)::date),
    version smallint NOT NULL DEFAULT 1 CHECK (version > 0),
    tipo_fuente varchar(30) NOT NULL,
    institucion_reportada_raw text NOT NULL,
    lugar_reportado_raw text NULL,
    centro_poblado_reportado_raw text NULL,
    nivel_modalidad_reportada_raw text NULL,
    turno_reportado_raw text NULL,
    hoja_pagina_origen varchar(60) NOT NULL DEFAULT '1',
    region_origen text NULL,
    indice_bloque integer NOT NULL DEFAULT 1 CHECK (indice_bloque > 0),
    metodo_match_ie varchar(30) NULL,
    score_match_ie numeric(5,4) NULL CHECK (score_match_ie BETWEEN 0 AND 1),
    estado_match_ie varchar(20) NOT NULL DEFAULT 'PENDIENTE',
    estado varchar(30) NOT NULL DEFAULT 'IMPORTADO',
    validado_por bigint NULL REFERENCES usuario(usuario_id),
    validado_en timestamptz NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (documento_recibido_id, hoja_pagina_origen, indice_bloque, version),
    CHECK (tipo_fuente IN ('EXCEL_NATIVO', 'PDF_NATIVO', 'PDF_ESCANEADO', 'FOTO')),
    CHECK (estado IN ('IMPORTADO', 'EN_VALIDACION', 'VALIDADO', 'HISTORICA', 'RECHAZADO')),
    CHECK (estado_match_ie IN ('PENDIENTE', 'RESUELTO', 'AMBIGUO', 'SIN_MATCH')),
    CHECK (reporte_asistencia_serie_id IS NULL OR institucion_educativa_id IS NOT NULL)
);
CREATE INDEX ix_reporte_asistencia_ie_periodo ON reporte_asistencia (institucion_educativa_id, periodo);
CREATE INDEX ix_reporte_asistencia_padre ON reporte_asistencia (version_padre_id);
CREATE UNIQUE INDEX uq_reporte_asistencia_serie_vigente
    ON reporte_asistencia (reporte_asistencia_serie_id) WHERE estado = 'VALIDADO';
CREATE UNIQUE INDEX uq_reporte_asistencia_serie_version
    ON reporte_asistencia (reporte_asistencia_serie_id, version) WHERE reporte_asistencia_serie_id IS NOT NULL;

CREATE OR REPLACE FUNCTION fn_reporte_asistencia_check_coherencia() RETURNS trigger AS $$
DECLARE
    v_serie_ie bigint; v_serie_periodo date;
    v_cal_ie bigint; v_cal_anio smallint; v_cal_estado varchar(20);
BEGIN
    IF NEW.reporte_asistencia_serie_id IS NOT NULL THEN
        SELECT institucion_educativa_id, periodo INTO v_serie_ie, v_serie_periodo
        FROM reporte_asistencia_serie WHERE reporte_asistencia_serie_id = NEW.reporte_asistencia_serie_id;
        IF v_serie_ie IS DISTINCT FROM NEW.institucion_educativa_id OR v_serie_periodo IS DISTINCT FROM NEW.periodo THEN
            RAISE EXCEPTION 'la serie % es de otra institucion/periodo', NEW.reporte_asistencia_serie_id;
        END IF;
    END IF;
    IF NEW.calendarizacion_version_id IS NOT NULL THEN
        -- ADR-014 de v1: compara institucion_educativa_id directo, no via local_educativo_id
        -- (asi corrigio v1 un bug real: dos IE del mismo local pasaban esta validacion igual).
        SELECT cl.institucion_educativa_id, cl.anio, cv.estado INTO v_cal_ie, v_cal_anio, v_cal_estado
        FROM calendarizacion_version cv JOIN calendarizacion_local cl ON cl.calendarizacion_local_id = cv.calendarizacion_local_id
        WHERE cv.calendarizacion_version_id = NEW.calendarizacion_version_id;
        IF v_cal_estado NOT IN ('VIGENTE', 'HISTORICA') THEN
            RAISE EXCEPTION 'el calendario % no esta VIGENTE/HISTORICA (estado %), no se puede usar en un reporte', NEW.calendarizacion_version_id, v_cal_estado;
        END IF;
        IF NEW.institucion_educativa_id IS NOT NULL AND v_cal_ie IS DISTINCT FROM NEW.institucion_educativa_id THEN
            RAISE EXCEPTION 'el calendario % es de otra institucion educativa', NEW.calendarizacion_version_id;
        END IF;
        IF v_cal_anio IS DISTINCT FROM EXTRACT(YEAR FROM NEW.periodo)::smallint THEN
            RAISE EXCEPTION 'el calendario % es de otro anio', NEW.calendarizacion_version_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_reporte_asistencia_check_coherencia
    BEFORE INSERT OR UPDATE ON reporte_asistencia
    FOR EACH ROW EXECUTE FUNCTION fn_reporte_asistencia_check_coherencia();

CREATE TABLE trabajador_en_reporte (
    trabajador_en_reporte_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reporte_asistencia_id uuid NOT NULL REFERENCES reporte_asistencia(reporte_asistencia_id),
    trabajador_id bigint NULL REFERENCES trabajador(trabajador_id),
    vinculo_trabajador_ie_id bigint NULL REFERENCES vinculo_trabajador_ie(vinculo_trabajador_ie_id),
    rol_laboral_id smallint NULL REFERENCES catalogo_rol_laboral(rol_laboral_id),
    fila_detalle_origen integer NULL CHECK (fila_detalle_origen > 0),
    fila_resumen_origen integer NULL CHECK (fila_resumen_origen > 0),
    dni_reportado_raw text NULL,
    nombres_reportados_raw text NOT NULL,
    cargo_reportado_raw text NULL,
    especialidad_reportada_raw text NULL,
    condicion_reportada_raw text NULL,
    telefono_reportado_raw text NULL,
    correo_reportado_raw text NULL,
    jornada_horas_reportada numeric(6,2) NULL CHECK (jornada_horas_reportada >= 0),
    metodo_match varchar(30) NULL,
    score_match numeric(5,4) NULL CHECK (score_match BETWEEN 0 AND 1),
    estado_match varchar(20) NOT NULL DEFAULT 'PENDIENTE',
    revisado_por bigint NULL REFERENCES usuario(usuario_id),
    revisado_en timestamptz NULL,
    resolucion_manual_por bigint NULL REFERENCES usuario(usuario_id),
    resolucion_manual_en timestamptz NULL,
    resolucion_manual_motivo text NULL,
    CHECK (fila_detalle_origen IS NOT NULL OR fila_resumen_origen IS NOT NULL),
    CHECK (estado_match IN ('PENDIENTE', 'RESUELTO', 'AMBIGUO', 'SIN_MATCH')),
    CHECK (estado_match <> 'RESUELTO' OR (trabajador_id IS NOT NULL AND rol_laboral_id IS NOT NULL)),
    CHECK (resolucion_manual_por IS NULL OR (resolucion_manual_en IS NOT NULL AND resolucion_manual_motivo IS NOT NULL))
);
CREATE UNIQUE INDEX uq_trabajador_en_reporte_detalle
    ON trabajador_en_reporte (reporte_asistencia_id, fila_detalle_origen) WHERE fila_detalle_origen IS NOT NULL;
CREATE UNIQUE INDEX uq_trabajador_en_reporte_resumen
    ON trabajador_en_reporte (reporte_asistencia_id, fila_resumen_origen) WHERE fila_resumen_origen IS NOT NULL;
CREATE INDEX ix_trabajador_en_reporte_trabajador ON trabajador_en_reporte (trabajador_id, reporte_asistencia_id);

-- fn_trabajador_en_reporte_check_coherencia: version ya corregida desde el diseño (decision de
-- vigencia §13.2): lee institucion_educativa_id directo de vinculo_trabajador_ie, sin JOIN a plaza
-- (el JOIN interno original de v1 excluia los vinculos sin plaza_id -- POR_REPORTE, manuales o
-- automaticos -- y los rechazaba con una excepcion falsa).
CREATE OR REPLACE FUNCTION fn_trabajador_en_reporte_check_coherencia() RETURNS trigger AS $$
DECLARE v_vinculo_trabajador bigint; v_vinculo_ie bigint; v_vinculo_rol smallint; v_reporte_ie bigint;
BEGIN
    IF NEW.vinculo_trabajador_ie_id IS NOT NULL THEN
        SELECT v.trabajador_id, v.institucion_educativa_id, v.rol_laboral_id
        INTO v_vinculo_trabajador, v_vinculo_ie, v_vinculo_rol
        FROM vinculo_trabajador_ie v
        WHERE v.vinculo_trabajador_ie_id = NEW.vinculo_trabajador_ie_id;

        IF NEW.trabajador_id IS NOT NULL AND v_vinculo_trabajador IS DISTINCT FROM NEW.trabajador_id THEN
            RAISE EXCEPTION 'el vinculo % pertenece a otro trabajador', NEW.vinculo_trabajador_ie_id;
        END IF;

        IF NEW.rol_laboral_id IS NULL THEN
            NEW.rol_laboral_id := v_vinculo_rol;
        ELSIF NEW.rol_laboral_id IS DISTINCT FROM v_vinculo_rol THEN
            RAISE EXCEPTION 'el vinculo % tiene rol distinto del indicado en la fila', NEW.vinculo_trabajador_ie_id;
        END IF;

        SELECT institucion_educativa_id INTO v_reporte_ie
        FROM reporte_asistencia WHERE reporte_asistencia_id = NEW.reporte_asistencia_id;

        IF v_reporte_ie IS NOT NULL AND v_vinculo_ie IS DISTINCT FROM v_reporte_ie THEN
            RAISE EXCEPTION 'el vinculo % no pertenece a la institucion del reporte %',
                NEW.vinculo_trabajador_ie_id, NEW.reporte_asistencia_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_trabajador_en_reporte_check_coherencia
    BEFORE INSERT OR UPDATE ON trabajador_en_reporte
    FOR EACH ROW EXECUTE FUNCTION fn_trabajador_en_reporte_check_coherencia();

CREATE TABLE hecho_asistencia_dia (
    hecho_asistencia_dia_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trabajador_id bigint NOT NULL REFERENCES trabajador(trabajador_id),
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa(institucion_educativa_id),
    rol_laboral_id smallint NOT NULL REFERENCES catalogo_rol_laboral(rol_laboral_id),
    fecha date NOT NULL,
    estado_asistencia_id smallint NOT NULL REFERENCES catalogo_estado_asistencia(estado_asistencia_id),
    es_disputado boolean NOT NULL DEFAULT false,
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trabajador_id, institucion_educativa_id, rol_laboral_id, fecha)
);
CREATE INDEX ix_hecho_asistencia_dia_fecha ON hecho_asistencia_dia (fecha, estado_asistencia_id);

CREATE TABLE asistencia_dia (
    asistencia_dia_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trabajador_en_reporte_id uuid NOT NULL REFERENCES trabajador_en_reporte(trabajador_en_reporte_id),
    dia_calendarizacion_id uuid NULL REFERENCES dia_calendarizacion(dia_calendarizacion_id),
    hecho_asistencia_dia_id uuid NULL REFERENCES hecho_asistencia_dia(hecho_asistencia_dia_id),
    fecha date NOT NULL,
    celda_origen varchar(20) NULL,
    codigo_reportado_raw text NULL,
    estado_asistencia_id smallint NULL REFERENCES catalogo_estado_asistencia(estado_asistencia_id),
    estado_captura varchar(20) NOT NULL,
    minutos_tardanza integer NULL CHECK (minutos_tardanza >= 0),
    es_dia_laborable_esperado boolean NULL,
    observacion text NULL,
    confianza numeric(5,4) NULL CHECK (confianza BETWEEN 0 AND 1),
    modelo_extraccion text NULL,
    version_extraccion text NULL,
    validado boolean NOT NULL DEFAULT false,
    UNIQUE (trabajador_en_reporte_id, fecha),
    -- 'DERIVADO' agregado desde el diseño (ADR-019 de v1): distingue un dia que el sistema dedujo
    -- (del ANEXO 4 sin ambiguedad, o de la calendarizacion para una fecha fuera de la grilla) de
    -- uno que el director efectivamente escribio.
    CHECK (estado_captura IN ('REGISTRADO', 'VACIO', 'ILEGIBLE', 'NO_APLICA', 'PENDIENTE', 'DERIVADO')),
    CHECK (estado_captura NOT IN ('REGISTRADO', 'DERIVADO') OR estado_asistencia_id IS NOT NULL)
);
CREATE INDEX ix_asistencia_dia_fecha ON asistencia_dia (fecha, estado_asistencia_id);
CREATE INDEX ix_asistencia_dia_hecho ON asistencia_dia (hecho_asistencia_dia_id);

CREATE TABLE resumen_asistencia_reportado (
    resumen_asistencia_reportado_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trabajador_en_reporte_id uuid NOT NULL UNIQUE REFERENCES trabajador_en_reporte(trabajador_en_reporte_id),
    inasistencias_dias_raw text NULL,
    tardanza_horas_raw text NULL,
    tardanza_minutos_raw text NULL,
    permiso_sin_goce_horas_raw text NULL,
    permiso_sin_goce_minutos_raw text NULL,
    huelga_paro_dias_raw text NULL,
    observaciones_raw text NULL
);

CREATE TABLE validacion_reporte (
    validacion_reporte_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reporte_asistencia_id uuid NOT NULL REFERENCES reporte_asistencia(reporte_asistencia_id),
    trabajador_en_reporte_id uuid NULL REFERENCES trabajador_en_reporte(trabajador_en_reporte_id),
    fecha date NULL,
    codigo_regla varchar(60) NOT NULL,
    severidad varchar(20) NOT NULL,
    mensaje text NOT NULL,
    evidencia jsonb NOT NULL DEFAULT '{}'::jsonb,
    estado varchar(20) NOT NULL DEFAULT 'PENDIENTE',
    resuelta_por bigint NULL REFERENCES usuario(usuario_id),
    resuelta_en timestamptz NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CHECK (severidad IN ('INFORMATIVA', 'ADVERTENCIA', 'ERROR')),
    CHECK (estado IN ('PENDIENTE', 'RESUELTA', 'DESCARTADA')),
    CHECK (estado = 'PENDIENTE' OR (resuelta_por IS NOT NULL AND resuelta_en IS NOT NULL))
);
-- Reutilizada tal cual (decision de vigencia §13.1) para el codigo_regla
-- 'INSTITUCION_ACTUALIZADA_AUTOMATICAMENTE' -- codigo_regla no tiene CHECK de dominio, admite un
-- valor nuevo sin migracion.

CREATE OR REPLACE FUNCTION fn_asistencia_dia_resolver_hecho() RETURNS trigger AS $$
DECLARE
    v_trabajador_id bigint; v_ie_id bigint; v_rol_id smallint;
    v_hecho_id uuid; v_hecho_estado smallint;
BEGIN
    IF NEW.estado_captura NOT IN ('REGISTRADO', 'DERIVADO') THEN
        RETURN NULL;
    END IF;

    -- Guarda de recursion: el UPDATE de mas abajo (hecho_asistencia_dia_id = v_hecho_id) dispara
    -- este mismo trigger AFTER UPDATE otra vez. Sin esta guarda, la segunda invocacion vuelve a
    -- calcular y a reescribir el mismo valor, lo que dispara una tercera, y asi indefinidamente
    -- hasta agotar la pila de Postgres -- nunca se habia ejercitado esta ruta con datos reales
    -- hasta construir el importador de asistencia.
    IF NEW.hecho_asistencia_dia_id IS NOT NULL THEN
        RETURN NULL;
    END IF;

    SELECT ter.trabajador_id, ra.institucion_educativa_id, ter.rol_laboral_id
    INTO v_trabajador_id, v_ie_id, v_rol_id
    FROM trabajador_en_reporte ter
    JOIN reporte_asistencia ra ON ra.reporte_asistencia_id = ter.reporte_asistencia_id
    WHERE ter.trabajador_en_reporte_id = NEW.trabajador_en_reporte_id;

    IF v_trabajador_id IS NULL OR v_ie_id IS NULL OR v_rol_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT hecho_asistencia_dia_id, estado_asistencia_id INTO v_hecho_id, v_hecho_estado
    FROM hecho_asistencia_dia
    WHERE trabajador_id = v_trabajador_id AND institucion_educativa_id = v_ie_id
      AND rol_laboral_id = v_rol_id AND fecha = NEW.fecha
    FOR UPDATE;

    IF v_hecho_id IS NULL THEN
        INSERT INTO hecho_asistencia_dia (trabajador_id, institucion_educativa_id, rol_laboral_id, fecha, estado_asistencia_id)
        VALUES (v_trabajador_id, v_ie_id, v_rol_id, NEW.fecha, NEW.estado_asistencia_id)
        RETURNING hecho_asistencia_dia_id INTO v_hecho_id;
    ELSIF v_hecho_estado IS DISTINCT FROM NEW.estado_asistencia_id THEN
        UPDATE hecho_asistencia_dia SET es_disputado = true, actualizado_en = now() WHERE hecho_asistencia_dia_id = v_hecho_id;
        INSERT INTO validacion_reporte (reporte_asistencia_id, trabajador_en_reporte_id, fecha, codigo_regla, severidad, mensaje, evidencia)
        SELECT ter.reporte_asistencia_id, NEW.trabajador_en_reporte_id, NEW.fecha, 'ASISTENCIA_CONTRADICTORIA', 'ERROR',
               format('Evidencia contradictoria para trabajador %s, IE %s, rol %s, fecha %s', v_trabajador_id, v_ie_id, v_rol_id, NEW.fecha),
               jsonb_build_object('hecho_asistencia_dia_id', v_hecho_id, 'estado_previo', v_hecho_estado, 'estado_nuevo', NEW.estado_asistencia_id)
        FROM trabajador_en_reporte ter WHERE ter.trabajador_en_reporte_id = NEW.trabajador_en_reporte_id;
    END IF;

    UPDATE asistencia_dia SET hecho_asistencia_dia_id = v_hecho_id WHERE asistencia_dia_id = NEW.asistencia_dia_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_asistencia_dia_resolver_hecho
    AFTER INSERT OR UPDATE ON asistencia_dia
    FOR EACH ROW EXECUTE FUNCTION fn_asistencia_dia_resolver_hecho();

-- ============================================================
-- 5. Consolidado DRE
-- ============================================================

CREATE TABLE consolidado_dre (
    consolidado_dre_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    periodo date NOT NULL CHECK (periodo = date_trunc('month', periodo)::date),
    nivel_modalidad varchar(80) NOT NULL,
    version smallint NOT NULL DEFAULT 1 CHECK (version > 0),
    estado varchar(20) NOT NULL DEFAULT 'BORRADOR',
    rectifica_a_id uuid NULL REFERENCES consolidado_dre(consolidado_dre_id),
    generado_por bigint NULL REFERENCES usuario(usuario_id),
    generado_en timestamptz NOT NULL DEFAULT now(),
    enviado_por bigint NULL REFERENCES usuario(usuario_id),
    enviado_en timestamptz NULL,
    archivo_exportado_id uuid NULL REFERENCES objeto_archivo(objeto_archivo_id),
    UNIQUE (periodo, nivel_modalidad, version),
    CHECK (estado IN ('BORRADOR', 'ENVIADO')),
    CHECK ((estado = 'ENVIADO') = (enviado_en IS NOT NULL AND enviado_por IS NOT NULL)),
    CHECK (estado <> 'ENVIADO' OR archivo_exportado_id IS NOT NULL)
);
-- nivel_modalidad se agrega respecto al DDL heredado: el pendiente 4 confirmado el 2026-09-10
-- (docs/casos/TACTAMAL_JULIO_2026.md §11) fija que el output es un consolidado por NIVEL
-- educativo y mes, no uno solo por mes -- coincide con los archivos reales encontrados en
-- data/raw/data_brindada_por_ugel/asistencia_marzo_2026_a_junio_2026/.

CREATE TABLE consolidado_dre_detalle (
    consolidado_dre_detalle_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    consolidado_dre_id uuid NOT NULL REFERENCES consolidado_dre(consolidado_dre_id),
    institucion_educativa_id bigint NOT NULL REFERENCES institucion_educativa(institucion_educativa_id),
    trabajador_id bigint NOT NULL REFERENCES trabajador(trabajador_id),
    rol_laboral_id smallint NOT NULL REFERENCES catalogo_rol_laboral(rol_laboral_id),
    -- agregado tras revision de modelo (2026-09-10): un trabajador puede tener mas de un vinculo
    -- concurrente en la misma institucion y rol (titular + encargatura en otra plaza, o
    -- CUADRO_DE_HORAS + POR_REEMPLAZO simultaneos -- casos reales confirmados en NEXUS, no un
    -- error de importacion). Sin esta columna en el grano, el segundo vinculo del mismo
    -- trabajador se perdia silenciosamente al insertar (ON CONFLICT DO NOTHING sobre trabajador+
    -- institucion+rol) y su carga de dias esperados quedaria invisible para el consolidado.
    -- Nullable: un trabajador_en_reporte puede tener trabajador_id/rol_laboral_id resueltos sin
    -- que fn_resolver_vinculo_por_reporte haya podido resolver un vinculo (ver esa funcion).
    vinculo_trabajador_ie_id bigint NULL REFERENCES vinculo_trabajador_ie(vinculo_trabajador_ie_id),
    dias_lectivos_esperados integer NULL CHECK (dias_lectivos_esperados >= 0),
    dias_gestion_esperados integer NULL CHECK (dias_gestion_esperados >= 0),
    corresponde_descuento boolean NULL,
    decidido_por bigint NULL REFERENCES usuario(usuario_id),
    decidido_en timestamptz NULL,
    motivo_decision text NULL,
    observacion text NULL,
    fuente_calculo jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (consolidado_dre_id, trabajador_id, institucion_educativa_id, rol_laboral_id, vinculo_trabajador_ie_id),
    CHECK (corresponde_descuento IS NULL OR (decidido_por IS NOT NULL AND decidido_en IS NOT NULL AND motivo_decision IS NOT NULL))
);

CREATE TABLE consolidado_dre_detalle_estado (
    consolidado_dre_detalle_estado_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    consolidado_dre_detalle_id uuid NOT NULL REFERENCES consolidado_dre_detalle(consolidado_dre_detalle_id),
    estado_asistencia_id smallint NOT NULL REFERENCES catalogo_estado_asistencia(estado_asistencia_id),
    cantidad_dias numeric(6,2) NULL CHECK (cantidad_dias >= 0),
    cantidad_minutos integer NULL CHECK (cantidad_minutos >= 0),
    UNIQUE (consolidado_dre_detalle_id, estado_asistencia_id)
);

CREATE TABLE consolidado_dre_detalle_fuente (
    consolidado_dre_detalle_fuente_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    consolidado_dre_detalle_id uuid NOT NULL REFERENCES consolidado_dre_detalle(consolidado_dre_detalle_id),
    reporte_asistencia_id uuid NULL REFERENCES reporte_asistencia(reporte_asistencia_id),
    trabajador_en_reporte_id uuid NULL REFERENCES trabajador_en_reporte(trabajador_en_reporte_id),
    calendarizacion_version_id uuid NULL REFERENCES calendarizacion_version(calendarizacion_version_id),
    vinculo_trabajador_ie_id bigint NULL REFERENCES vinculo_trabajador_ie(vinculo_trabajador_ie_id),
    CHECK (reporte_asistencia_id IS NOT NULL OR trabajador_en_reporte_id IS NOT NULL OR calendarizacion_version_id IS NOT NULL)
);
CREATE INDEX ix_consolidado_dre_detalle_fuente_detalle ON consolidado_dre_detalle_fuente (consolidado_dre_detalle_id);

-- ============================================================
-- 6. Auditoria append-only
-- ============================================================

CREATE OR REPLACE FUNCTION fn_trabajador_auditoria() RETURNS trigger AS $$
BEGIN
    INSERT INTO trabajador_auditoria (trabajador_id, operacion, valores_anteriores, valores_nuevos, modificado_por)
    VALUES (
        COALESCE(OLD.trabajador_id, NEW.trabajador_id), TG_OP,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
        NULLIF(current_setting('app.usuario_id', true), '')::bigint
    );
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_trabajador_auditoria
    AFTER INSERT OR UPDATE OR DELETE ON trabajador
    FOR EACH ROW EXECUTE FUNCTION fn_trabajador_auditoria();

CREATE OR REPLACE FUNCTION fn_vinculo_trabajador_ie_auditoria() RETURNS trigger AS $$
BEGIN
    INSERT INTO vinculo_trabajador_ie_auditoria (vinculo_trabajador_ie_id, operacion, valores_anteriores, valores_nuevos, modificado_por)
    VALUES (
        COALESCE(OLD.vinculo_trabajador_ie_id, NEW.vinculo_trabajador_ie_id), TG_OP,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
        NULLIF(current_setting('app.usuario_id', true), '')::bigint
    );
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_vinculo_trabajador_ie_auditoria
    AFTER INSERT OR UPDATE OR DELETE ON vinculo_trabajador_ie
    FOR EACH ROW EXECUTE FUNCTION fn_vinculo_trabajador_ie_auditoria();

CREATE OR REPLACE FUNCTION fn_auditoria_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% es append-only: no se edita ni elimina', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_trabajador_auditoria_append_only
    BEFORE UPDATE OR DELETE ON trabajador_auditoria
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();
CREATE TRIGGER trg_vinculo_auditoria_append_only
    BEFORE UPDATE OR DELETE ON vinculo_trabajador_ie_auditoria
    FOR EACH ROW EXECUTE FUNCTION fn_auditoria_append_only();

CREATE OR REPLACE FUNCTION fn_objeto_archivo_freeze() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'objeto_archivo es inmutable: no se elimina';
    END IF;
    RAISE EXCEPTION 'objeto_archivo es inmutable: no se edita, se crea un objeto nuevo';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_objeto_archivo_freeze
    BEFORE UPDATE OR DELETE ON objeto_archivo FOR EACH ROW EXECUTE FUNCTION fn_objeto_archivo_freeze();

CREATE OR REPLACE FUNCTION fn_documento_recibido_freeze() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'documento_recibido es inmutable: no se elimina';
    END IF;
    RAISE EXCEPTION 'documento_recibido es inmutable: no se edita, se crea un registro de recepcion nuevo';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_documento_recibido_freeze
    BEFORE UPDATE OR DELETE ON documento_recibido FOR EACH ROW EXECUTE FUNCTION fn_documento_recibido_freeze();

CREATE OR REPLACE FUNCTION fn_set_actualizado_en() RETURNS trigger AS $$
BEGIN
    NEW.actualizado_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_institucion_educativa_actualizado_en
    BEFORE UPDATE ON institucion_educativa FOR EACH ROW EXECUTE FUNCTION fn_set_actualizado_en();
CREATE TRIGGER trg_trabajador_actualizado_en
    BEFORE UPDATE ON trabajador FOR EACH ROW EXECUTE FUNCTION fn_set_actualizado_en();
CREATE TRIGGER trg_local_educativo_actualizado_en
    BEFORE UPDATE ON local_educativo FOR EACH ROW EXECUTE FUNCTION fn_set_actualizado_en();

-- PII
COMMENT ON TABLE trabajador IS 'PII: DNI y datos personales. Acceso restringido.';
COMMENT ON COLUMN nexus_registro.documento_identidad_raw IS 'PII cruda.';
COMMENT ON COLUMN trabajador_en_reporte.dni_reportado_raw IS 'PII cruda.';
COMMENT ON COLUMN trabajador_en_reporte.telefono_reportado_raw IS 'PII cruda.';
COMMENT ON COLUMN trabajador_en_reporte.correo_reportado_raw IS 'PII cruda.';

-- ============================================================
-- 7. Semillas de catalogo (valores reales ya verificados en docs/casos/)
-- ============================================================

INSERT INTO catalogo_rol_laboral (codigo, nombre) VALUES
    ('DOCENTE', 'Docente'),
    ('ADMINISTRATIVO', 'Administrativo'),
    ('PEC', 'PEC'),
    ('CAS', 'CAS');

INSERT INTO catalogo_tipo_dia (codigo_interno, nombre) VALUES
    ('LECTIVO', 'Lectivo'),
    ('GESTION', 'Gestion'),
    ('NO_LECTIVO_NI_GESTION', 'No lectivo ni de gestion');

-- Mapeo de codigos crudos L/G/D observados en los calendarios reales de Tactamal y Membrillo
-- (docs/casos/TACTAMAL_JULIO_2026.md, docs/casos/IE_258_MEMBRILLO_JUNIO_2026.md).
INSERT INTO catalogo_codigo_tipo_dia_fuente (familia_formato, codigo_raw, tipo_dia_id)
SELECT 'UGEL_LUYA_CALENDARIO_2026', codigo_raw, tipo_dia_id
FROM (VALUES ('L', 'LECTIVO'), ('G', 'GESTION'), ('D', 'NO_LECTIVO_NI_GESTION')) AS m(codigo_raw, codigo_interno)
JOIN catalogo_tipo_dia USING (codigo_interno);

-- Catalogo de asistencia: los 11 codigos vistos en Tactamal julio 2026 (incluye C, U ausentes en
-- Membrillo/R-003) -- ver docs/casos/TACTAMAL_JULIO_2026.md §4. No se declara exhaustivo
-- (PRODUCT.md §7.1 sigue pendiente de confirmacion oficial con la UGEL).
INSERT INTO catalogo_estado_asistencia (codigo, nombre, categoria) VALUES
    ('A', 'Dia laborado', 'ASISTENCIA'),
    ('I', 'Inasistencia injustificada', 'INASISTENCIA'),
    ('3T', 'Tercera tardanza (inasistencia injustificada)', 'INASISTENCIA'),
    ('J', 'Inasistencia justificada', 'INASISTENCIA'),
    ('L', 'Licencia con goce de remuneraciones', 'LICENCIA'),
    ('P', 'Permiso sin goce de remuneraciones', 'PERMISO'),
    ('T', 'Tardanza', 'TARDANZA'),
    ('H', 'Huelga o paro', 'HUELGA'),
    ('F', 'Feriado', 'FERIADO'),
    ('C', 'Comision de servicio con papeleta de salida', 'COMISION'),
    ('U', 'Capacitacion autorizada por la UGEL', 'CAPACITACION');
