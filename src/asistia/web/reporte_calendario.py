"""Consulta del calendario propio, limitada al año y mes declarados en el reporte."""

from .instituciones import calendario_institucion


def calendario_reporte(conn, reporte, fechas):
    iid, periodo = reporte["institucion_educativa_id"], reporte["periodo"]
    disponible = (
        iid
        and conn.execute(
            """SELECT 1 FROM calendarizacion_local cl
        JOIN calendarizacion_version cv USING(calendarizacion_local_id)
        WHERE cl.institucion_educativa_id=%s AND cl.anio=%s
        AND cv.estado NOT IN ('HISTORICA','RECHAZADA') LIMIT 1""",
            (iid, periodo.year),
        ).fetchone()
    )
    if not disponible:
        return None
    vista = calendario_institucion(conn, iid, anio=periodo.year, mes=periodo.month)
    mes = vista["anual"]["meses"][periodo.month - 1]
    dias = [d for d in mes["celdas"] if d]
    return {
        "version": vista["calendario"],
        "ultima": vista["edicion_calendario"],
        "nombre_mes": mes["nombre"],
        "dias": dias,
        "leyenda": vista["anual"]["leyenda"],
        "revision": vista["revision_calendario"],
        # Solo se sincronizan columnas cuando representan exactamente las mismas fechas.
        "alineado": list(fechas) == [d["fecha"] for d in dias],
    }
