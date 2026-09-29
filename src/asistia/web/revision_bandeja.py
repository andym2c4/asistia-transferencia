"""Bandeja mensual a grano de institución, sin confundir recepción con obligación."""

from . import ErrorDeTrabajo, lecturas


def instituciones_revision(conn, periodo, nivel):
    reportes = lecturas.reportes_mes(conn, periodo, nivel)
    ids = list({r["institucion_educativa_id"] for r in reportes})
    instituciones = conn.execute(
        """SELECT institucion_educativa_id,nombre_ie,cod_mod,anexo,distrito,
                  centro_poblado,nivel_modalidad
           FROM institucion_educativa
           WHERE fn_nivel_canonico(nivel_modalidad)=%s
              OR institucion_educativa_id=ANY(%s::bigint[])
           ORDER BY nombre_ie,cod_mod,anexo""",
        (nivel, ids),
    ).fetchall()
    por_ie = {
        i["institucion_educativa_id"]: {**i, "reportes": []} for i in instituciones
    }
    for reporte in reportes:
        por_ie[reporte["institucion_educativa_id"]]["reportes"].append(reporte)
    for institucion in por_ie.values():
        actuales = institucion["reportes"]
        institucion["estado"] = (
            "Sin reporte cargado"
            if not actuales
            else "Digitalización revisada"
            if all(r["estado"] == "VALIDADO" for r in actuales)
            else "Por revisar"
        )
        for key in ("filas", "sin_identidad", "pendientes", "criticos"):
            institucion[key] = sum(r[key] for r in actuales)
    return list(por_ie.values())


def contexto_carga(conn, valor):
    """Contexto de navegación; nunca sustituye la identidad extraída de la fuente."""
    if valor is None:
        return None
    try:
        iid = int(valor)
        if not 0 < iid < 2**63:
            raise ValueError
    except (ValueError, TypeError):
        raise ErrorDeTrabajo("La institución seleccionada no es válida.") from None
    return lecturas.uno(
        conn,
        "SELECT institucion_educativa_id,nombre_ie,cod_mod,anexo FROM institucion_educativa WHERE institucion_educativa_id=%s",
        (iid,),
    )
