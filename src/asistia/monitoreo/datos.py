"""Corte consistente de fuentes operativas; no reclasifica documentos existentes."""

from __future__ import annotations

import calendar
import copy
from collections import Counter, defaultdict
from datetime import date

from asistia import categorias
from asistia.ajustes_asistencia import ajuste_del_dia, marca_efectiva
from asistia.experimental.datos import huella
from asistia.leyendas import categoria_dia, normal

from .reglas import agregar, declaracion, evaluar


def cargar_fuentes(conn, anio, *, reporte_ids=None):
    if not 2000 <= anio <= 2100:
        raise ValueError("El año debe estar entre 2000 y 2100.")
    filtro = (
        "AND (r.institucion_educativa_id,r.periodo) IN (SELECT institucion_educativa_id,periodo FROM reporte_asistencia WHERE reporte_asistencia_id=ANY(%s::uuid[]))"
        if reporte_ids is not None
        else ""
    )
    parametros = (date(anio, 1, 1), date(anio + 1, 1, 1))
    if reporte_ids is not None:
        parametros += ([str(r) for r in reporte_ids],)
    reportes = conn.execute(
        f"""SELECT r.reporte_asistencia_id,r.reporte_asistencia_serie_id,r.version,
        r.institucion_educativa_id,r.periodo,r.estado,r.clasificacion_codigos,
        r.documento_recibido_id,r.hoja_pagina_origen,r.procedencia_extraccion,
        o.sha256,d.nombre_original,ie.cod_mod,ie.anexo,ie.nombre_ie,
        fn_nivel_canonico(ie.nivel_modalidad) AS nivel
        FROM reporte_asistencia r JOIN institucion_educativa ie USING(institucion_educativa_id)
        JOIN documento_recibido d USING(documento_recibido_id)
        JOIN objeto_archivo o USING(objeto_archivo_id)
        WHERE r.periodo >= %s AND r.periodo < %s
        {filtro}
        AND r.estado NOT IN ('RECHAZADO','HISTORICA')
        AND NOT EXISTS (SELECT 1 FROM reporte_asistencia h
          WHERE h.reporte_asistencia_serie_id=r.reporte_asistencia_serie_id AND h.version>r.version)
        ORDER BY r.periodo,r.institucion_educativa_id,r.reporte_asistencia_id""",
        parametros,
    ).fetchall()
    ids = [r["reporte_asistencia_id"] for r in reportes]
    trabajadores = conn.execute(
        """SELECT trabajador_en_reporte_id,reporte_asistencia_id,trabajador_id,
        vinculo_trabajador_ie_id,rol_laboral_id,fila_detalle_origen
        FROM trabajador_en_reporte WHERE reporte_asistencia_id=ANY(%s)
        ORDER BY trabajador_en_reporte_id""",
        (ids,),
    ).fetchall()
    dias = conn.execute(
        """SELECT a.asistencia_dia_id,a.trabajador_en_reporte_id,a.fecha,a.celda_origen,
        a.codigo_reportado_raw,a.codigo_interpretado,a.evidencia_interpretacion,a.estado_captura,a.validado
        FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
        WHERE t.reporte_asistencia_id=ANY(%s) ORDER BY a.trabajador_en_reporte_id,a.fecha""",
        (ids,),
    ).fetchall()
    vigencias = conn.execute(
        """WITH fechas AS (
          SELECT DISTINCT t.vinculo_trabajador_ie_id AS vid,r.periodo+n AS fecha
          FROM trabajador_en_reporte t JOIN reporte_asistencia r USING(reporte_asistencia_id)
          CROSS JOIN LATERAL generate_series(0,extract(day from r.periodo+interval '1 month - 1 day')::int-1) n
          WHERE r.reporte_asistencia_id=ANY(%s) AND t.vinculo_trabajador_ie_id IS NOT NULL)
        SELECT vid,fecha,fn_vinculo_presencia_esperada(vid,fecha) AS esperado
        FROM fechas ORDER BY vid,fecha""",
        (ids,),
    ).fetchall()
    calendarios = conn.execute(
        """SELECT DISTINCT ON (cl.institucion_educativa_id) cv.calendarizacion_version_id,
        cl.institucion_educativa_id,cl.anio,cv.version,cv.estado,cv.procedencia_extraccion,
        cv.clasificacion_codigos,cv.documento_recibido_id,o.sha256,d.nombre_original
        FROM calendarizacion_local cl JOIN calendarizacion_version cv USING(calendarizacion_local_id)
        LEFT JOIN documento_recibido d USING(documento_recibido_id)
        LEFT JOIN objeto_archivo o USING(objeto_archivo_id)
        WHERE cl.anio=%s AND cl.institucion_educativa_id=ANY(%s)
        AND cv.estado NOT IN ('RECHAZADA','HISTORICA')
        ORDER BY cl.institucion_educativa_id,
          (cv.procedencia_extraccion->>'tipo'='DERIVADO_2025') ASC NULLS FIRST,
          (cv.estado='VIGENTE') DESC,cv.version DESC""",
        (anio, sorted({r["institucion_educativa_id"] for r in reportes})),
    ).fetchall()
    programacion = conn.execute(
        """SELECT calendarizacion_version_id,fecha,codigo_reportado_raw,codigo_interpretado,
        evidencia_interpretacion,estado_captura,hoja_origen,celda_origen
        FROM dia_calendarizacion WHERE calendarizacion_version_id=ANY(%s)
        ORDER BY calendarizacion_version_id,fecha""",
        ([c["calendarizacion_version_id"] for c in calendarios],),
    ).fetchall()
    return {
        "anio": anio,
        "reportes": reportes,
        "trabajadores": trabajadores,
        "dias": dias,
        "vigencias": vigencias,
        "calendarios": calendarios,
        "programacion": programacion,
        "catalogo": categorias.catalogo(conn),
        "equivalencias": [
            e for d in categorias.DOMINIOS for e in categorias.equivalencias(conn, d)
        ],
    }


def clasificador(corte):
    reglas = {r["categoria_id"]: r for r in corte["catalogo"]}
    enlaces = {
        (e["dominio"], e["significado"]): e["categoria_id"]
        for e in corte["equivalencias"]
    }

    def clasificar(c, dominio):
        from asistia.calidad_leyendas import pendiente

        c = copy.deepcopy(c)
        if c.get("fuente") == "REVISION_WEB" or pendiente(c):
            return c
        original = categorias.fuente_original(c)
        significado = normal(original.get("tipo_dia"))
        cid = enlaces.get((dominio, significado))
        if cid in reglas and not original.get("conflictos"):
            return categorias.componer(original, reglas[cid], significado)
        return c

    return clasificar


def resumen_categoria(c):
    return {
        k: c[k]
        for k in (
            "tipo_dia",
            "es_remunerado",
            "es_falta",
            "grupo_actividad",
            "estado_asistencia_codigo",
            "fuente",
            "categoria_general",
            "conflicto_catalogo",
            "conflictos",
            "revision_leyenda",
        )
        if k in c
    }


def construir(
    corte, *, reglas_aplicadas=False, exigir_vigente=False, ajustes_rrhh=False
):
    """Agrupa observaciones antes de evaluar, sin multiplicar personas por vínculos."""
    clasificar = (
        (lambda c, dominio: copy.deepcopy(c))
        if reglas_aplicadas
        else clasificador(corte)
    )
    reportes = {r["reporte_asistencia_id"]: r for r in corte["reportes"]}
    calendarios = {r["institucion_educativa_id"]: r for r in corte["calendarios"]}
    programacion = {
        (d["calendarizacion_version_id"], d["fecha"]): d for d in corte["programacion"]
    }
    asistencia = {(d["trabajador_en_reporte_id"], d["fecha"]): d for d in corte["dias"]}
    vigencias = {(d["vid"], d["fecha"]): d["esperado"] for d in corte["vigencias"]}
    categorias_reportes = {
        rid: {
            cod: clasificar(c, "asistencia")
            for cod, c in r["clasificacion_codigos"].items()
        }
        for rid, r in reportes.items()
    }
    categorias_calendarios = {
        ie: {
            cod: clasificar(c, "calendario")
            for cod, c in r["clasificacion_codigos"].items()
        }
        for ie, r in calendarios.items()
    }
    perfiles, observaciones = {}, defaultdict(list)
    for r in reportes.values():
        clave = (r["institucion_educativa_id"], r["periodo"])
        p = perfiles.setdefault(
            clave,
            {
                "institucion_educativa_id": clave[0],
                "periodo": clave[1],
                "identidad": {
                    k: r[k] for k in ("cod_mod", "anexo", "nombre_ie", "nivel")
                },
                "fuentes": {"reportes": [], "calendario": calendarios.get(clave[0])},
                "dias": [],
            },
        )
        p["fuentes"]["reportes"].append(
            dict(
                r,
                clasificacion_analitica=categorias_reportes[r["reporte_asistencia_id"]],
            )
        )
    for t in corte["trabajadores"]:
        r = reportes[t["reporte_asistencia_id"]]
        mes = r["periodo"]
        for n in range(1, calendar.monthrange(mes.year, mes.month)[1] + 1):
            fecha = date(mes.year, mes.month, n)
            clave = (r["institucion_educativa_id"], mes)
            # Identidades desconocidas no se juntan por nombre ni se inventan.
            persona = t["trabajador_id"] or str(t["trabajador_en_reporte_id"])
            grano = (*clave, persona, t["rol_laboral_id"], fecha)
            ad = asistencia.get((t["trabajador_en_reporte_id"], fecha), {})
            ajuste = ajuste_del_dia(r, ad) if ajustes_rrhh else None
            if ajustes_rrhh:
                ad = marca_efectiva(r, ad)
            ca = categoria_dia(
                {
                    "clasificacion_codigos": categorias_reportes[
                        r["reporte_asistencia_id"]
                    ]
                },
                ad,
            )
            # Una corrección diaria tiene precedencia frente a la regla general.
            evidencia = ad.get("evidencia_interpretacion", {})
            if not evidencia.get("clasificacion_aceptada") and (
                evidencia.get("dato_no_determinado")
                or not (ad.get("codigo_interpretado") or ad.get("codigo_reportado_raw"))
            ):
                ca = {}
            observaciones[grano].append(
                {
                    "categoria": ca,
                    **({"ajuste_rrhh": ajuste} if ajuste else {}),
                    "estado_captura": ad.get("estado_captura"),
                    "no_aplica": ad.get("estado_captura") == "NO_APLICA",
                    "trabajador_id": t["trabajador_id"],
                    "rol_id": t["rol_laboral_id"],
                    "vigencia": vigencias.get((t["vinculo_trabajador_ie_id"], fecha)),
                    "reporte_id": str(r["reporte_asistencia_id"]),
                    "trabajador_en_reporte_id": str(t["trabajador_en_reporte_id"]),
                    "asistencia_dia_id": str(ad["asistencia_dia_id"]) if ad else None,
                    "vinculo_id": t["vinculo_trabajador_ie_id"],
                    "codigo_recibido": ad.get("codigo_reportado_raw"),
                    "codigo_interpretado": ad.get("codigo_interpretado"),
                    "localizador": ad.get("celda_origen"),
                    "fila": t["fila_detalle_origen"],
                }
            )
    for grano, obs in sorted(observaciones.items(), key=lambda kv: str(kv[0])):
        ie, mes, persona, rol, fecha = grano
        cv = calendarios.get(ie, {})
        cd = programacion.get((cv.get("calendarizacion_version_id"), fecha), {})
        cc = categoria_dia(
            {"clasificacion_codigos": categorias_calendarios.get(ie, {})}, cd
        )
        if not cd.get("evidencia_interpretacion", {}).get(
            "clasificacion_aceptada"
        ) and not (cd.get("codigo_interpretado") or cd.get("codigo_reportado_raw")):
            cc = {}
        # Una fila mensual sin observación diaria no contradice una declaración
        # diaria existente. Se conserva como fuente, pero no participa en la firma.
        observadas = [o for o in obs if o["asistencia_dia_id"] is not None] or obs
        # Comparar significado, sin confundir códigos diferentes con contradicción.
        firmas = {
            huella(
                {
                    "declaracion": declaracion(o["categoria"]),
                    "no_aplica": o["no_aplica"],
                    **{
                        k: o["categoria"].get(k)
                        for k in (
                            "es_remunerado",
                            "es_falta",
                            "conflictos",
                            "conflicto_catalogo",
                        )
                    },
                }
            )
            for o in observadas
        }
        exclusion = None
        if not obs[0]["trabajador_id"] or rol is None:
            exclusion = "IDENTIDAD_O_ROL_PENDIENTE"
        elif not any(o["vigencia"] is True for o in obs):
            exclusion = (
                "VIGENCIA_PENDIENTE"
                if any(o["vigencia"] is None for o in obs)
                else "SIN_VIGENCIA_ESPERADA"
            )
        ca = observadas[0]["categoria"] if len(firmas) == 1 else {}
        impedimento = exclusion or (
            "FUENTES_DIARIAS_CONTRADICTORIAS"
            if len(firmas) > 1
            else "SIN_CALENDARIO"
            if not cv
            else "CALENDARIO_DERIVADO_2025"
            if cv.get("procedencia_extraccion", {}).get("tipo") == "DERIVADO_2025"
            else None
        )
        from asistia.calidad_leyendas import pendiente

        if not impedimento and (pendiente(ca) or pendiente(cc)):
            impedimento = "LEYENDA_POR_REVISAR"
        if not impedimento and exigir_vigente and cv.get("estado") != "VIGENTE":
            impedimento = "CALENDARIO_SIN_CONFIRMAR"
        if any(o.get("ajuste_rrhh", {}).get("vigente") is False for o in observadas):
            impedimento = "AJUSTE_RRHH_OBSOLETO"
        evaluado = evaluar(
            cc,
            ca,
            impedimento=impedimento,
            no_aplica=len(firmas) == 1 and observadas[0]["no_aplica"],
        )
        dia = {
            "clave": f"{persona}:{rol}:{fecha}",
            "fecha": str(fecha),
            "trabajador_id": obs[0]["trabajador_id"],
            "rol_id": rol,
            "calendario": resumen_categoria(cc),
            "asistencia": resumen_categoria(ca),
            "codigo_calendario": cd.get("codigo_interpretado")
            or cd.get("codigo_reportado_raw"),
            "fuente_calendario": {
                "hoja": cd.get("hoja_origen"),
                "celda": cd.get("celda_origen"),
            },
            "fuentes": [{k: v for k, v in o.items() if k != "categoria"} for o in obs],
            "observaciones_unidas": len(obs),
            "exclusion": exclusion,
            **evaluado,
        }
        perfiles[(ie, mes)]["dias"].append(dia)
    salida = []
    for clave, p in sorted(perfiles.items()):
        p["datos"] = agregar(p["dias"])
        p["datos"].update(
            exclusiones=dict(
                Counter(d["exclusion"] for d in p["dias"] if d["exclusion"])
            ),
            pendientes=dict(
                Counter(
                    d["pendiente"]
                    for d in p["dias"]
                    if not d["exclusion"] and d["pendiente"]
                )
            ),
            observaciones_unidas=sum(d["observaciones_unidas"] - 1 for d in p["dias"]),
        )
        p["datos"]["dias"] = p.pop("dias")
        salida.append(p)
    return salida
