"""Resultados de la cola de reportes; la recepción/extracción sigue en cargas.py."""

from flask import url_for

from .lecturas import uno

ESTADOS = {
    "PROCESADO": "Digitalizado",
    "PARCIAL": "Por completar",
    "NO_SOPORTADO": "Revisar formato",
    "ERROR": "Requiere atención",
    "RECIBIDO": "Recibido · por procesar",
    "PROCESANDO": "Procesamiento por comprobar",
}


def resultado_carga(conn, cid, duplicado=False, aviso=None):
    carga = uno(conn, "SELECT * FROM web_carga WHERE web_carga_id=%s", (cid,))
    reportes = conn.execute(
        """SELECT r.reporte_asistencia_id,r.periodo,r.version,
                  coalesce(ie.nombre_ie,r.institucion_reportada_raw) AS nombre,
                  coalesce(s.nivel_modalidad,r.nivel_modalidad_reportada_raw,'Sin nivel') AS nivel,
                  coalesce(s.turno,r.turno_reportado_raw,'Sin turno') AS turno
           FROM reporte_asistencia r JOIN documento_recibido d USING(documento_recibido_id)
           LEFT JOIN institucion_educativa ie USING(institucion_educativa_id)
           LEFT JOIN reporte_asistencia_serie s USING(reporte_asistencia_serie_id)
           WHERE d.objeto_archivo_id=%s AND r.estado NOT IN ('HISTORICA','RECHAZADO')
           ORDER BY r.periodo,nombre,r.version DESC""",
        (carga["objeto_archivo_id"],),
    ).fetchall()
    url = url_for("pages.documento", cid=cid)
    cierre = carga["resultado"].get("cierre_documento_id")
    return {
        "carga_id": str(cid),
        "estado": carga["estado"],
        "titulo": ESTADOS.get(carga["estado"], carga["estado"]),
        "duplicado": duplicado,
        "mensaje": aviso or carga["mensaje"] or "Original conservado.",
        "url": url,
        "continuar_url": url_for("pages.documento_cierre", did=cierre)
        if cierre
        else url,
        "continuar_label": "Continuar lectura asistida" if cierre else "Ver resultado",
        "reportes": [
            {
                "etiqueta": f"{r['nombre']} · {r['periodo']:%m/%Y} · {r['nivel']} · {r['turno']} · v{r['version']}",
                "url": url_for("pages.reporte", rid=r["reporte_asistencia_id"]),
            }
            for r in reportes
        ],
    }


def cargas_recientes(conn, usuario_id):
    return conn.execute(
        """SELECT c.web_carga_id,c.estado,c.mensaje,r.nombre_original,r.cargado_en
           FROM web_carga c JOIN LATERAL (
               SELECT nombre_original,cargado_en FROM web_recepcion r
               WHERE r.web_carga_id=c.web_carga_id AND r.cargado_por=%s
               ORDER BY r.cargado_en DESC LIMIT 1
           ) r ON true
           WHERE c.tipo='asistencia' ORDER BY r.cargado_en DESC LIMIT 10""",
        (usuario_id,),
    ).fetchall()
