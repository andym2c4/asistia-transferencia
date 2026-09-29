"""Directorio y movimientos de personal. Contrato DIRECTORIO_RRHH_1.

Los movimientos cuentan personas distintas, tras deduplicar persona/institución/acción.
El mes es el de carga de la fuente (Lima), nunca la fecha efectiva de ingreso/cese.
No se deduce una baja de la ausencia en un corte NEXUS.
"""

import json
from collections import Counter
from datetime import date

from asistia.niveles import NIVELES

from . import ErrorDeTrabajo

ORIGENES = {
    "NEXUS": "Corte mensual · NEXUS",
    "MANUAL_RRHH": "Revisiones de RRHH",
    "AUTOMATICO_IMPORTACION": "Reportes · incorporación automática",
}
NIVELES_DIRECTORIO = {**NIVELES, "OTROS": "Otros niveles / sin clasificar"}
COLUMNAS = {
    "nombre_ie": "Institución",
    "distrito": "Distrito",
    "centro_poblado": "Centro poblado",
    "cod_mod": "Código modular",
    "personas": "Personal registrado",
    "calendarizacion": "Calendarización",
}


def filtros_columnas(raw):
    """Inclusión o exclusión explícita: desmarcar uno no serializa todo el padrón."""
    try:
        seleccion = json.loads(raw or "{}")
        if not isinstance(seleccion, dict) or len(raw) > 60000:
            raise ValueError
        for columna, regla in seleccion.items():
            if (
                columna not in COLUMNAS
                or not isinstance(regla, dict)
                or set(regla) != {"modo", "valores"}
                or regla["modo"] not in ("incluir", "excluir")
                or not isinstance(regla["valores"], list)
                or len(regla["valores"]) > 2000
                or any(not isinstance(v, str) or len(v) > 512 for v in regla["valores"])
            ):
                raise ValueError
        return seleccion
    except (ValueError, TypeError):
        raise ErrorDeTrabajo(
            "Los filtros de columnas no son válidos. Limpia los filtros y vuelve a intentarlo."
        ) from None


def valor_columna(institucion, columna):
    valor = institucion[columna]
    if columna == "calendarizacion":
        valor = valor["estado"]
    return "" if valor is None else str(valor)


def filtrar_columnas(instituciones, seleccion):
    """OR entre valores; AND entre columnas. Cada faceta excluye su propio filtro."""
    reglas = {k: (v["modo"], set(v["valores"])) for k, v in seleccion.items()}

    def coincide(i, excepto=None):
        return all(
            (valor_columna(i, k) in valores) == (modo == "incluir")
            for k, (modo, valores) in reglas.items()
            if k != excepto
        )

    facetas = []
    for columna, titulo in COLUMNAS.items():
        cuentas = Counter(
            valor_columna(i, columna) for i in instituciones if coincide(i, columna)
        )
        valores = cuentas.keys() | set(seleccion.get(columna, {}).get("valores", []))
        facetas.append(
            {
                "columna": columna,
                "titulo": titulo,
                "valores": [
                    {
                        "valor": v,
                        "etiqueta": v or "Sin registrar",
                        "cantidad": cuentas[v],
                    }
                    for v in sorted(valores)
                ],
            }
        )
    return [i for i in instituciones if coincide(i)], facetas


def filtros(args):
    nivel = args.get("nivel_ie", "")
    if nivel and nivel not in NIVELES_DIRECTORIO:
        raise ErrorDeTrabajo("Selecciona un nivel disponible en el directorio.")
    mes = args.get("actualizado", "")
    try:
        periodo = date.fromisoformat(mes + "-01") if mes else None
    except ValueError:
        raise ErrorDeTrabajo("El mes del directorio no es válido.") from None
    return {
        "q": args.get("q", "").strip()[:100],
        "nivel_ie": nivel,
        "actualizado": periodo,
        "columnas": filtros_columnas(args.get("fc", "")),
    }


def movimientos(conn):
    """Fuentes conservadas, sin joins de hechos diarios ni datos nominales.

    Un fin NEXUS procede del INSERT auditado del vínculo, para que una revisión
    posterior no aparezca retrospectivamente como dato de ese archivo.
    """
    cortes = conn.execute("""
        SELECT c.nexus_carga_id, c.fecha_corte, c.cargado_en,
               date_trunc('month',c.cargado_en AT TIME ZONE 'America/Lima')::date mes,
               c.estado_integridad, c.documento_recibido_id, d.nombre_original
        FROM nexus_carga c JOIN documento_recibido d USING(documento_recibido_id) ORDER BY c.fecha_corte DESC,c.cargado_en DESC
    """).fetchall()
    eventos = conn.execute("""
        SELECT DISTINCT n.trabajador_id, n.institucion_educativa_id,
               c.cargado_en instante, c.fecha_corte,
               date_trunc('month',c.cargado_en AT TIME ZONE 'America/Lima')::date mes,
               'NEXUS' origen, 'ALTA' accion, c.documento_recibido_id documento,
               (a.valores_nuevos->>'fecha_fin')::date fin_informado
        FROM nexus_registro n JOIN nexus_carga c USING(nexus_carga_id)
        LEFT JOIN vinculo_trabajador_ie_auditoria a
          ON a.vinculo_trabajador_ie_id=n.vinculo_trabajador_ie_id AND a.operacion='INSERT'
        WHERE c.estado_integridad='COMPLETA'
          AND n.estado_resolucion='RESUELTO'
        ORDER BY instante
    """).fetchall()
    corte_inicial = min(
        (c["fecha_corte"] for c in cortes if c["estado_integridad"] == "COMPLETA"),
        default=None,
    )
    for e in eventos:
        e["inicial"] = e["fecha_corte"] == corte_inicial
    eventos += [
        dict(e, accion="BAJA", inicial=False)
        for e in eventos
        if e["fin_informado"] and corte_inicial < e["fin_informado"] <= e["fecha_corte"]
    ]
    eventos += conn.execute("""
        SELECT v.trabajador_id,v.institucion_educativa_id,c.confirmado_en instante,
               date_trunc('month',COALESCE(w.cargado_en,o.primera_vez_visto_en)
                          AT TIME ZONE 'America/Lima')::date mes,
               CASE WHEN c.origen='AUTOMATICO_IMPORTACION' AND EXISTS (
                   SELECT 1 FROM validacion_reporte a
                   WHERE a.trabajador_en_reporte_id=t.trabajador_en_reporte_id
                     AND a.codigo_regla IN ('INSTITUCION_ACTUALIZADA_AUTOMATICAMENTE',
                         'TRABAJADOR_SIN_NEXUS_VINCULADO_AUTOMATICAMENTE')
                     AND a.estado='RESUELTA' AND a.resuelta_por IS NOT NULL
               ) THEN 'MANUAL_RRHH' ELSE c.origen END origen,
               CASE c.accion WHEN 'CONFIRMA_INICIO' THEN 'ALTA' ELSE 'BAJA' END accion,
               r.documento_recibido_id documento
        FROM vinculo_trabajador_ie_confirmacion c
        JOIN vinculo_trabajador_ie v USING(vinculo_trabajador_ie_id)
        JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
        JOIN reporte_asistencia r USING(reporte_asistencia_id)
        JOIN documento_recibido d USING(documento_recibido_id)
        JOIN objeto_archivo o USING(objeto_archivo_id)
        LEFT JOIN LATERAL (
          SELECT min(wr.cargado_en) cargado_en FROM web_recepcion wr
          JOIN web_carga wc USING(web_carga_id)
          WHERE wc.objeto_archivo_id=d.objeto_archivo_id AND wc.tipo='asistencia'
        ) w ON true
        WHERE v.trabajador_id IS NOT NULL AND r.estado <> 'RECHAZADO'
    """).fetchall()
    # Una reafirmación, otro rol/plaza o reimportación no vuelve a ser un ingreso.
    unicos = {}
    for e in sorted(
        eventos,
        key=lambda e: (
            not e.get("inicial", False),
            e["instante"],
            e["origen"],
            str(e["documento"]),
        ),
    ):
        clave = (e["trabajador_id"], e["institucion_educativa_id"], e["accion"])
        anterior = unicos.get(clave)
        if anterior is None:
            unicos[clave] = e
        elif (
            anterior["origen"] == "AUTOMATICO_IMPORTACION"
            and e["origen"] == "MANUAL_RRHH"
        ):
            # La revisión acepta el mismo hecho; conserva el mes original de carga.
            unicos[clave] = dict(e, mes=anterior["mes"])
    return cortes, list(unicos.values())


def directorio(conn, *, q="", nivel_ie="", actualizado=None, columnas=None):
    from .calendario_anual import estados_directorio

    instituciones = conn.execute("""
        SELECT ie.institucion_educativa_id,ie.nombre_ie,ie.cod_mod,ie.anexo,
               ie.nivel_modalidad,ie.distrito,ie.centro_poblado,
               COALESCE(fn_nivel_canonico(ie.nivel_modalidad),'OTROS') nivel_grupo,
               (SELECT count(DISTINCT v.trabajador_id) FROM vinculo_trabajador_ie v
                WHERE v.institucion_educativa_id=ie.institucion_educativa_id) personas
        FROM institucion_educativa ie ORDER BY ie.nombre_ie,ie.cod_mod,ie.anexo
    """).fetchall()
    estados = estados_directorio(conn)
    for i in instituciones:
        i["calendarizacion"] = estados.get(
            i["institucion_educativa_id"], {"estado": "Pendiente", "anio": None}
        )
    niveles = Counter(i["nivel_grupo"] for i in instituciones)
    tipos = Counter(
        i["nivel_modalidad"]
        for i in instituciones
        if not nivel_ie or i["nivel_grupo"] == nivel_ie
    )
    seleccion = [
        i
        for i in instituciones
        if (not nivel_ie or i["nivel_grupo"] == nivel_ie)
        and (
            not q
            or q.casefold()
            in " ".join(
                str(i[k] or "")
                for k in ("nombre_ie", "cod_mod", "distrito", "centro_poblado")
            ).casefold()
        )
    ]
    columnas = columnas or {}
    seleccion, facetas = filtrar_columnas(seleccion, columnas)
    ids = {i["institucion_educativa_id"] for i in seleccion}
    cortes, eventos = movimientos(conn)
    meses = sorted(
        {e["mes"] for e in eventos} | {c["mes"] for c in cortes}, reverse=True
    )
    mes = actualizado or (meses[0] if meses else None)
    if mes and mes not in meses:
        meses = sorted([*meses, mes], reverse=True)
    del_mes = [
        e for e in eventos if e["mes"] == mes and e["institucion_educativa_id"] in ids
    ]

    def contar(accion, origen=None):
        return len(
            {
                e["trabajador_id"]
                for e in del_mes
                if e["accion"] == accion
                and not e.get("inicial", False)
                and (origen is None or e["origen"] == origen)
            }
        )

    total = len(seleccion)
    return {
        "instituciones": seleccion,
        "columnas_directorio": COLUMNAS,
        "filtros_columnas": columnas,
        "fc": json.dumps(columnas, ensure_ascii=False, separators=(",", ":"))
        if columnas
        else "",
        "facetas": facetas,
        "total": total,
        "total_padron": len(instituciones),
        "niveles_directorio": NIVELES_DIRECTORIO,
        "conteos_nivel": niveles,
        "tipos": tipos,
        "meses": meses,
        "mes_actualizado": mes,
        "nuevos": contar("ALTA"),
        "bajas": contar("BAJA"),
        "base_inicial": len(
            {e["trabajador_id"] for e in del_mes if e.get("inicial", False)}
        ),
        "origenes": [
            {
                "codigo": k,
                "nombre": v,
                "nuevos": contar("ALTA", k),
                "bajas": contar("BAJA", k),
            }
            for k, v in ORIGENES.items()
        ],
        "cortes_mes": [c for c in cortes if c["mes"] == mes],
        "ultimo_corte": max(
            (c["fecha_corte"] for c in cortes if c["estado_integridad"] == "COMPLETA"),
            default=None,
        ),
        "q": q,
        "nivel_ie": nivel_ie,
    }


def reportes_institucion(conn, iid):
    """Una fila por serie; sin serie, cada reporte conserva su identidad propia."""
    return conn.execute(
        """
        WITH versiones AS (
          SELECT r.*, row_number() OVER (
            PARTITION BY COALESCE(reporte_asistencia_serie_id,reporte_asistencia_id)
            ORDER BY version DESC,creado_en DESC,reporte_asistencia_id) orden,
            count(*) OVER (
              PARTITION BY COALESCE(reporte_asistencia_serie_id,reporte_asistencia_id)) versiones
          FROM reporte_asistencia r WHERE institucion_educativa_id=%s
        )
        SELECT r.*,d.nombre_original,o.primera_vez_visto_en AT TIME ZONE 'America/Lima' cargado_en,s.turno
        FROM versiones r JOIN documento_recibido d USING(documento_recibido_id)
        JOIN objeto_archivo o USING(objeto_archivo_id)
        LEFT JOIN reporte_asistencia_serie s USING(reporte_asistencia_serie_id)
        WHERE r.orden=1
        ORDER BY r.periodo DESC,r.creado_en DESC,r.reporte_asistencia_id
    """,
        (iid,),
    ).fetchall()


def calendario_institucion(conn, iid, *, anio=None, mes=None, mes_preferido=1):
    """Vigente del año elegido o última versión consultable, nunca otra institución."""
    from .calendario_anual import calendario_anual
    from .calendario_revision import propuesta

    try:
        anio = int(anio) if anio else None
        mes = int(mes) if mes else None
        if mes is not None and not 1 <= mes <= 12:
            raise ValueError
    except ValueError:
        raise ErrorDeTrabajo(
            "Selecciona un año disponible y un mes válido para el calendario."
        ) from None
    versiones = conn.execute(
        """
        SELECT cv.*,cl.anio FROM calendarizacion_local cl
        JOIN calendarizacion_version cv USING(calendarizacion_local_id)
        WHERE cl.institucion_educativa_id=%s
        ORDER BY cl.anio DESC,(cv.estado='VIGENTE') DESC,cv.version DESC
    """,
        (iid,),
    ).fetchall()
    disponibles = [
        v for v in versiones if v["estado"] not in {"HISTORICA", "RECHAZADA"}
    ]
    anios = sorted({v["anio"] for v in disponibles}, reverse=True)
    if anio is not None and anio not in anios:
        raise ErrorDeTrabajo(
            "Ese año no tiene un calendario disponible para esta institución."
        )
    anio = anio or (anios[0] if anios else None)
    del_anio = [v for v in disponibles if v["anio"] == anio]
    cv = del_anio[0] if del_anio else None
    dias = (
        conn.execute(
            """
        SELECT d.*,c.nombre tipo,c.codigo_interno grupo_recibido
        FROM dia_calendarizacion d LEFT JOIN catalogo_tipo_dia c USING(tipo_dia_id)
        WHERE calendarizacion_version_id=%s ORDER BY fecha
    """,
            (cv["calendarizacion_version_id"],),
        ).fetchall()
        if cv
        else []
    )
    meses_recibidos = {d["fecha"].month for d in dias}
    mes = mes or (
        mes_preferido
        if mes_preferido in meses_recibidos
        else min(meses_recibidos, default=mes_preferido)
    )
    return {
        "calendario": cv,
        "revision_calendario": propuesta(conn, cv["calendarizacion_version_id"])
        if cv
        else None,
        "anios_calendario": anios,
        "anio_calendario": anio,
        "edicion_calendario": max(
            (v for v in versiones if v["anio"] == anio), key=lambda v: v["version"]
        )
        if cv
        else None,
        "anual": calendario_anual(cv, dias) if cv else None,
        "mes_visible": mes,
        "editable_dias": False,
        "calendario_consulta": True,
    }
