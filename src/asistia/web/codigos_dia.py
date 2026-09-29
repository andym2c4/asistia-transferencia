"""Clasificación de códigos de calendario no reconocidos (feedback RRHH 2026-09-12: instituciones
usan códigos propios como "P" que quedan CODIGO_DESCONOCIDO sin que nadie pueda enseñarle al
sistema qué significan). No reclasifica retroactivamente días ya importados -- una fuente nueva
no debe alterar silenciosamente un consolidado ya revisado; aplica desde la próxima importación o
reimportación del mismo formato."""

from __future__ import annotations

from . import ErrorDeTrabajo
from .lecturas import uno


def codigos_desconocidos(conn, familia_formato: str):
    return conn.execute(
        "SELECT d.codigo_reportado_raw AS codigo_raw, count(*) AS dias, "
        "min(d.fecha) AS primera_fecha, max(d.fecha) AS ultima_fecha, "
        "count(DISTINCT cv.calendarizacion_version_id) AS calendarios "
        "FROM dia_calendarizacion d JOIN calendarizacion_version cv USING(calendarizacion_version_id) "
        "WHERE d.estado_captura='CODIGO_DESCONOCIDO' "
        "AND NOT EXISTS (SELECT 1 FROM catalogo_codigo_tipo_dia_fuente f "
        "WHERE f.familia_formato=%s AND f.codigo_raw=d.codigo_reportado_raw AND f.vigente_hasta IS NULL) "
        "GROUP BY d.codigo_reportado_raw ORDER BY count(*) DESC",
        (familia_formato,),
    ).fetchall()


def tipos_dia(conn):
    return conn.execute(
        "SELECT tipo_dia_id,codigo_interno,nombre FROM catalogo_tipo_dia WHERE activo ORDER BY tipo_dia_id"
    ).fetchall()


def clasificaciones_vigentes(conn, familia_formato: str):
    return conn.execute(
        "SELECT f.*,t.nombre AS tipo_nombre,u.nombre AS autor FROM catalogo_codigo_tipo_dia_fuente f "
        "JOIN catalogo_tipo_dia t USING(tipo_dia_id) LEFT JOIN usuario u ON u.usuario_id=f.creado_por "
        "WHERE f.familia_formato=%s AND f.vigente_hasta IS NULL ORDER BY f.codigo_raw",
        (familia_formato,),
    ).fetchall()


def clasificar_codigo(
    conn, familia_formato: str, codigo_raw: str, tipo_dia_id: int, motivo: str, uid: int
):
    codigo_raw = (codigo_raw or "").strip()
    if not codigo_raw:
        raise ErrorDeTrabajo("Indica el código tal como aparece en el original.")
    uno(
        conn,
        "SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE tipo_dia_id=%s AND activo",
        (tipo_dia_id,),
    )
    existente = conn.execute(
        "SELECT 1 FROM catalogo_codigo_tipo_dia_fuente "
        "WHERE familia_formato=%s AND codigo_raw=%s AND vigente_hasta IS NULL",
        (familia_formato, codigo_raw),
    ).fetchone()
    if existente:
        raise ErrorDeTrabajo(
            f"El código «{codigo_raw}» ya está clasificado. Para cambiarlo se necesita cerrar "
            "la vigencia anterior; por ahora no está disponible desde esta pantalla.",
            409,
        )
    conn.execute(
        "INSERT INTO catalogo_codigo_tipo_dia_fuente"
        "(familia_formato,codigo_raw,tipo_dia_id,vigente_desde,creado_por,motivo) "
        "VALUES (%s,%s,%s,CURRENT_DATE,%s,%s)",
        (familia_formato, codigo_raw, tipo_dia_id, uid, motivo or None),
    )
