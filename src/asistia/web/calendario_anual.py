"""Consulta anual compacta; conserva categorías locales y desconocidos de la fuente."""

import calendar
from collections import Counter
from datetime import date

from asistia.leyendas import categoria_dia

from .calendario_mensual import GRUPOS, MESES

ESTADOS = {
    "SIN_ASIGNAR": "Sin asignar por revisión",
    "VACIO": "Vacío recibido",
    "ILEGIBLE": "Código ilegible",
    "CODIGO_DESCONOCIDO": "Código sin determinar",
    "NO_APLICA": "No aplica",
}


def avisos_categoria(c):
    avisos = []
    if not c.get("tipo_dia") or c.get("grupo_actividad") not in GRUPOS:
        avisos.append("Falta confirmar la categoría y su actividad")
    if c.get("es_remunerado") is None:
        avisos.append("Falta confirmar la remuneración")
    if any(c.get(k) for k in ("revision_leyenda", "conflictos", "conflicto_catalogo")):
        avisos.append("La categoría tiene observaciones por revisar")
    return avisos


def calendario_anual(cv, dias):
    por_fecha = {d["fecha"]: d for d in dias}
    leyenda = dict(cv["clasificacion_codigos"])
    for d in dias:
        if d.get("estado_captura") == "SIN_ASIGNAR":
            continue
        codigo = d.get("codigo_interpretado") or d.get("codigo_reportado_raw")
        if codigo:
            leyenda.setdefault(codigo, {})
    # Los códigos largos reciben un identificador visual, conservando su texto en la leyenda.
    usados = {c for c in leyenda if len(c) <= 3}
    simbolos = {}
    for codigo in sorted(leyenda):
        simbolo = codigo
        if len(codigo) > 3:
            n = 1
            while str(n) in usados:
                n += 1
            simbolo = str(n)
            usados.add(simbolo)
        simbolos[codigo] = simbolo
    meses, total = [], Counter()
    pendientes_revision = False
    for numero, nombre in enumerate(MESES, 1):
        cantidad = calendar.monthrange(cv["anio"], numero)[1]
        celdas = [None] * date(cv["anio"], numero, 1).weekday()
        conteos = Counter()
        for n in range(1, cantidad + 1):
            fecha = date(cv["anio"], numero, n)
            d = por_fecha.get(fecha)
            c = categoria_dia(cv, d) if d else {}
            codigo = (
                (d.get("codigo_interpretado") or d.get("codigo_reportado_raw"))
                if d
                else None
            )
            captura = d.get("estado_captura") if d else None
            sin_asignar = captura == "SIN_ASIGNAR"
            if sin_asignar:
                codigo = None
            conflicto = any(
                c.get(k)
                for k in ("revision_leyenda", "conflictos", "conflicto_catalogo")
            )
            grupo = (
                (d.get("grupo_recibido") or c.get("grupo_actividad"))
                if d and captura == "REGISTRADO" and not conflicto
                else None
            )
            grupo = (
                "NO_APLICA"
                if captura == "NO_APLICA"
                else grupo
                if grupo in GRUPOS
                else ""
            )
            conteos[grupo] += 1
            avisos = avisos_categoria(c) if codigo else []
            excepcion = bool(
                d
                and d.get("evidencia_interpretacion", {}).get("clasificacion_aceptada")
            )
            if captura != "REGISTRADO":
                avisos.insert(0, ESTADOS.get(captura, "Sin registro en la fuente"))
            if excepcion:
                avisos.append(
                    "Categoría retirada por revisión; consulta su fuente"
                    if sin_asignar
                    else "Regla propia de este día; consulta su fuente"
                )
            if (
                numero > 2
                and captura != "NO_APLICA"
                and (not grupo or avisos_categoria(c))
            ):
                pendientes_revision = True
            estado = ESTADOS.get(captura, "Sin registro" if not d else "Asignado")
            detalle = [fecha.strftime("%d/%m/%Y"), c.get("tipo_dia") or estado]
            if codigo:
                detalle.append("Código: " + codigo)
            if sin_asignar and d.get("codigo_reportado_raw") is not None:
                detalle.append(
                    "Código recibido conservado: " + d["codigo_reportado_raw"]
                )
            detalle.extend(avisos)
            if d:
                detalle.append(
                    "Fuente: "
                    + " · ".join(
                        str(d[k]) for k in ("hoja_origen", "celda_origen") if d.get(k)
                    )
                )
            celdas.append(
                {
                    "fecha": fecha,
                    "simbolo": "∅"
                    if sin_asignar
                    else "—"
                    if captura == "NO_APLICA"
                    else simbolos.get(
                        codigo, "○" if captura == "VACIO" else "!" if d else "·"
                    ),
                    "grupo": grupo,
                    "aviso": excepcion and not sin_asignar,
                    "detalle": ". ".join(dict.fromkeys(detalle)),
                }
            )
        celdas.extend([None] * (42 - len(celdas)))
        meses.append(
            {"numero": numero, "nombre": nombre, "celdas": celdas, "conteos": conteos}
        )
        total.update(conteos)
    return {
        "meses": meses,
        "totales": total,
        "leyenda": [
            {
                "codigo": k,
                "simbolo": simbolos[k],
                "nombre": c.get("tipo_dia") or "Categoría sin confirmar",
                "grupo": c.get("grupo_actividad")
                if not any(
                    c.get(x)
                    for x in ("revision_leyenda", "conflictos", "conflicto_catalogo")
                )
                else "",
                "avisos": avisos_categoria(c),
            }
            for k, c in sorted(leyenda.items())
        ],
        "pendientes_revision": pendientes_revision
        or any(avisos_categoria(c) for c in leyenda.values()),
    }


def estados_directorio(conn):
    """Una versión por institución; la misma preferencia de año/vigente del detalle."""
    versiones = conn.execute("""
        SELECT DISTINCT ON (cl.institucion_educativa_id)
            cl.institucion_educativa_id,cl.anio,cv.calendarizacion_version_id,cv.version,cv.estado,
            cv.clasificacion_codigos,cv.procedencia_extraccion->>'tipo' tipo_origen,
            (SELECT max(v.version) FROM calendarizacion_version v
             WHERE v.calendarizacion_local_id=cl.calendarizacion_local_id) ultima
        FROM calendarizacion_local cl JOIN calendarizacion_version cv USING(calendarizacion_local_id)
        WHERE cv.estado NOT IN ('HISTORICA','RECHAZADA')
        ORDER BY cl.institucion_educativa_id,cl.anio DESC,(cv.estado='VIGENTE') DESC,cv.version DESC
    """).fetchall()
    vigentes = [
        v["calendarizacion_version_id"] for v in versiones if v["estado"] == "VIGENTE"
    ]
    dias_por_version = {}
    if vigentes:
        for d in conn.execute(
            """SELECT d.*,c.codigo_interno grupo_recibido FROM dia_calendarizacion d
            LEFT JOIN catalogo_tipo_dia c USING(tipo_dia_id) WHERE calendarizacion_version_id=ANY(%s)""",
            (vigentes,),
        ).fetchall():
            dias_por_version.setdefault(d["calendarizacion_version_id"], []).append(d)
    estados = {}
    for cv in versiones:
        okey = (
            cv["estado"] == "VIGENTE"
            and cv["version"] == cv["ultima"]
            and cv["tipo_origen"] != "DERIVADO_2025"
            and not calendario_anual(
                cv, dias_por_version.get(cv["calendarizacion_version_id"], [])
            )["pendientes_revision"]
        )
        estados[cv["institucion_educativa_id"]] = {
            "estado": "Okey" if okey else "Por revisar",
            "anio": cv["anio"],
        }
    return estados
