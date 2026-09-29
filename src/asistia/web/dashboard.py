"""Lectura de gestión: avance actual y sugerencias de un corte conservado."""

from collections import Counter
from datetime import date
from math import isfinite
from uuid import UUID

from asistia.coherencia import revisar_reportes
from asistia.monitoreo.cortes import codigo
from asistia.monitoreo.modelo import VERSION
from asistia.monitoreo.reglas import REGLAS, VARIABLES

from . import ErrorDeTrabajo, lecturas
from .calendario_mensual import MESES

CONTRATO = "DASHBOARD_RRHH_1"


def porcentaje(numerador, denominador):
    return round(100 * numerador / denominador, 1) if denominador else None


def meses(conn, periodo, nivel):
    """Últimas series elegibles; cada porcentaje conserva sus componentes."""
    totales = conn.execute(
        """WITH ultimos AS (
          SELECT DISTINCT ON (s.reporte_asistencia_serie_id) s.periodo,r.estado
          FROM reporte_asistencia_serie s JOIN reporte_asistencia r USING(reporte_asistencia_serie_id)
          WHERE s.periodo >= %s AND s.periodo < %s AND fn_nivel_canonico(s.nivel_modalidad)=%s
          AND r.estado NOT IN ('RECHAZADO','HISTORICA')
          ORDER BY s.reporte_asistencia_serie_id,r.version DESC)
        SELECT periodo,count(*) AS recibidos,count(*) FILTER(WHERE estado='VALIDADO') AS revisados
        FROM ultimos GROUP BY periodo""",
        (date(periodo.year, 1, 1), date(periodo.year + 1, 1, 1), nivel),
    ).fetchall()
    cortes = conn.execute(
        """SELECT DISTINCT ON (c.periodo) c.periodo,c.version,w.web_salida_id,w.estado_revision,w.creado_en
        FROM web_salida w JOIN consolidado_dre c USING(consolidado_dre_id)
        WHERE c.periodo >= %s AND c.periodo < %s AND fn_nivel_canonico(c.nivel_modalidad)=%s
        ORDER BY c.periodo,c.version DESC,w.creado_en DESC,w.web_salida_id DESC""",
        (date(periodo.year, 1, 1), date(periodo.year + 1, 1, 1), nivel),
    ).fetchall()
    por_mes = {r["periodo"]: r for r in totales}
    por_salida = {r["periodo"]: r for r in cortes}
    filas = [
        {
            "periodo": (mes := date(periodo.year, n, 1)),
            "nombre": f"{n:02d}-{MESES[n - 1].upper()}",
            "recibidos": (total := por_mes.get(mes, {}).get("recibidos", 0)),
            "revisados": (revisados := por_mes.get(mes, {}).get("revisados", 0)),
            "porcentaje": porcentaje(revisados, total),
            "salida": por_salida.get(mes),
        }
        for n in range(1, 13)
    ]
    return [
        m
        for m in reversed(filas)
        if m["recibidos"] or m["salida"] or m["periodo"] == periodo
    ]


def prioridades(reportes, coherencias):
    """Una fila por institución; casos compartidos entre turnos no se suman dos veces."""
    instituciones = {}
    for r in reportes:
        c = coherencias[str(r["reporte_asistencia_id"])]
        grupo = instituciones.setdefault(
            r["institucion_educativa_id"],
            {
                **{
                    k: r[k]
                    for k in (
                        "institucion_educativa_id",
                        "nombre_ie",
                        "cod_mod",
                        "anexo",
                    )
                },
                "reportes": [],
                "casos": {},
                "impedimentos": set(),
                "digitalizacion": 0,
            },
        )
        pendiente_digital = (
            r["estado"] != "VALIDADO" or r["criticos"] or r["sin_identidad"]
        )
        if pendiente_digital or not c["listo"]:
            grupo["reportes"].append(
                {**r, "revisar_digitalizacion": bool(pendiente_digital)}
            )
        if pendiente_digital:
            grupo["digitalizacion"] += 1
        grupo["casos"].update(
            {caso["id"]: caso for caso in c["casos"] if not caso["resuelto"]}
        )
        grupo["impedimentos"].update(i["motivo"] for i in c["impedimentos"])
    filas = []
    for g in instituciones.values():
        if not g["reportes"]:
            continue
        reglas = Counter(
            regla["codigo"] for caso in g["casos"].values() for regla in caso["reglas"]
        )
        g.update(
            casos_pendientes=len(g["casos"]),
            motivos=[
                {"texto": REGLAS[codigo], "casos": n}
                for codigo, n in sorted(reglas.items())
            ],
            impedimentos=sorted(g["impedimentos"]),
            tipo="Discrepancias"
            if g["casos"]
            else "Información pendiente"
            if g["impedimentos"]
            else "Digitalización pendiente",
        )
        del g["casos"]
        filas.append(g)
    return sorted(
        filas,
        key=lambda g: (
            -g["casos_pendientes"],
            -bool(g["impedimentos"]),
            g["nombre_ie"],
            g["cod_mod"],
            g["anexo"],
        ),
    )


def datos_para_cotejar(datos, variables):
    """Valores observados, no atribuciones causales de la puntuación IF."""
    componentes = [
        (clave, c)
        for clave, c in datos.get("componentes", {}).items()
        if clave in variables
        and c.get("denominador")
        and c.get("valor") is not None
        and c["valor"] > 0
    ]
    componentes.sort(key=lambda item: (-item[1]["valor"], item[0]))
    return [
        {
            "texto": variables[clave],
            "numerador": c["numerador"],
            "denominador": c["denominador"],
        }
        for clave, c in componentes[:2]
    ]


def analisis(conn, periodo, nivel, elegido=None):
    cortes = conn.execute(
        "SELECT corte_id,creado_en FROM monitoreo_diario_corte WHERE anio=%s ORDER BY creado_en DESC,corte_id DESC",
        (periodo.year,),
    ).fetchall()
    if elegido:
        try:
            cid = UUID(str(elegido))
        except (ValueError, TypeError):
            raise ErrorDeTrabajo(
                "Selecciona un análisis disponible para este año."
            ) from None
        if cid not in {c["corte_id"] for c in cortes}:
            raise ErrorDeTrabajo("El análisis no corresponde al año seleccionado.", 404)
    elif cortes:
        cid = cortes[0]["corte_id"]
    else:
        return {"corte": None, "cortes": [], "filas": [], "total": 0}
    c = conn.execute(
        "SELECT corte_id,creado_en,manifiesto,resultados FROM monitoreo_diario_corte WHERE corte_id=%s",
        (cid,),
    ).fetchone()
    perfiles = conn.execute(
        "SELECT perfil_id,institucion_educativa_id,identidad,datos - 'dias' AS datos FROM monitoreo_diario_perfil WHERE corte_id=%s AND periodo=%s AND identidad->>'nivel'=%s",
        (cid, periodo, nivel),
    ).fetchall()
    puntuaciones = {r["reporte_id"]: r for r in c["resultados"]}
    entrenado = c["manifiesto"]["evaluacion"]["estado"] == "ENTRENADO"
    config = c["manifiesto"].get("configuracion_corte", {})
    anterior = config.get("version") != VERSION or config.get("codigo") != codigo()
    seleccionadas, puntuados, sin_datos = [], 0, 0
    for p in perfiles:
        if not p["datos"]["apto_modelo"]:
            sin_datos += 1
            continue
        score = puntuaciones.get(str(p["perfil_id"]))
        if (
            not entrenado
            or not score
            or not isinstance(score.get("score_if"), (float, int))
            or not isfinite(score["score_if"])
        ):
            continue
        puntuados += 1
        if score["en_cupo"]:
            seleccionadas.append(
                {
                    **p,
                    **p["identidad"],
                    "puesto": score["puesto"],
                    "observados": datos_para_cotejar(
                        p["datos"], c["manifiesto"].get("variables", VARIABLES)
                    ),
                }
            )
    seleccionadas.sort(key=lambda p: (p["puesto"], p["cod_mod"], p["anexo"]))
    return {
        "corte": c,
        "cortes": cortes,
        "filas": seleccionadas,
        "total": len(perfiles),
        "puntuados": puntuados,
        "sin_datos": sin_datos,
        "entrenado": entrenado,
        "pendientes_puntuacion": len(perfiles) - sin_datos - puntuados,
        "version_anterior": anterior,
    }


def preparar(conn, periodo, nivel, corte=None):
    reportes = lecturas.reportes_mes(conn, periodo, nivel)
    coherencias = revisar_reportes(conn, reportes)
    instituciones = lecturas.instituciones_mes(conn, periodo, nivel)
    revisados = sum(r["estado"] == "VALIDADO" for r in reportes)
    listos = sum(
        r["estado"] == "VALIDADO"
        and not r["criticos"]
        and not r["sin_identidad"]
        and coherencias[str(r["reporte_asistencia_id"])]["listo"]
        for r in reportes
    )
    por_ie = {}
    for r in reportes:
        por_ie.setdefault(r["institucion_educativa_id"], []).append(r)
    # Recepción del mismo ámbito y serie usado por las tarjetas, no de otro nivel.
    for i in instituciones:
        i["recibido"] = i["institucion_educativa_id"] in por_ie
    modelo = analisis(conn, periodo, nivel, corte)
    for p in modelo["filas"]:
        p["reportes"] = por_ie.get(p["institucion_educativa_id"], [])
    return {
        "reportes": reportes,
        "instituciones": instituciones,
        "total_reportes": len(reportes),
        "revisados": revisados,
        "listos": listos,
        "porcentaje_revisados": porcentaje(revisados, len(reportes)),
        "porcentaje_listos": porcentaje(listos, len(reportes)),
        "sin_reporte": sum(not i["recibido"] for i in instituciones),
        "prioridades": prioridades(reportes, coherencias),
        "meses": meses(conn, periodo, nivel),
        "analisis": modelo,
    }
