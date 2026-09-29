"""Presentación de la revisión puntual; no altera extracción ni aprobación."""

from __future__ import annotations

import calendar
import re
from datetime import date
from pathlib import Path

from flask import url_for
from openpyxl.utils.cell import column_index_from_string

from asistia.calidad_leyendas import pendiente
from asistia.leyendas import categoria_dia, codigos_usados_asistencia

CAPTURAS = {
    "REGISTRADO": "Marca explícita",
    "DERIVADO": "Dato derivado",
    "VACIO": "Vacío en la fuente",
    "ILEGIBLE": "Ilegible",
    "PENDIENTE": "Lectura pendiente",
    "NO_APLICA": "No aplica",
}
SIMBOLOS = {"VACIO": "∅", "ILEGIBLE": "?", "PENDIENTE": "?", "NO_APLICA": "—"}


def posicion_celda(valor):
    """Solo referencias A1, nunca expresiones, hojas o rutas aportadas por la fuente."""
    match = re.fullmatch(
        r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,5})(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,5}))?",
        str(valor or ""),
    )
    if not match:
        return None
    columna, fila = column_index_from_string(match[1].upper()), int(match[2])
    if columna > 1000 or fila > 100000:
        return None
    return {"fila": fila, "columna": columna, "celda": f"{match[1].upper()}{fila}"}


def fuente_url(reporte, *, celda=None, hoja=None, fila=None):
    rid = reporte["reporte_asistencia_id"]
    ext = Path(reporte["nombre_original"]).suffix.lower()
    if ext in {".xlsx", ".xlsm", ".xls"}:
        args = {}
        if hoja:
            args["hoja"] = hoja
        posicion = posicion_celda(celda)
        if posicion:
            args.update(
                celda=posicion["celda"],
                fila=max(1, posicion["fila"] - 5),
                columna=max(1, posicion["columna"] - 5),
            )
        elif fila:
            args["fila"] = max(1, int(fila) - 3)
        return url_for("pages.fuente", rid=rid, **args)
    pagina = re.search(r"p[aá]gina\s*[:=]?\s*(\d+)", str(celda or ""), re.IGNORECASE)
    if not pagina and str(reporte.get("hoja_pagina_origen", "")).isdigit():
        pagina = re.match(r"(\d+)", str(reporte["hoja_pagina_origen"]))
    return url_for("pages.fuente", rid=rid) + (
        f"#page={int(pagina[1])}" if pagina and ext in {".pdf", ".docx"} else ""
    )


def etiqueta_dia(dia):
    if dia.get("estado_captura") in SIMBOLOS:
        return SIMBOLOS[dia["estado_captura"]]
    return (
        dia.get("codigo_interpretado")
        or dia.get("codigo_reportado_raw")
        or dia.get("codigo")
        or "?"
    )


def motivo_categoria(categoria):
    if pendiente(categoria):
        return (
            "Comprueba la alineación entre el código y su significado en el original."
        )
    if categoria.get("conflictos") or categoria.get("conflicto_catalogo"):
        return "Hay interpretaciones diferentes de este código. Coteja la leyenda y registra la que corresponde."
    if not categoria.get("tipo_dia"):
        return "Falta identificar el significado de este código en la leyenda."
    if categoria.get("es_remunerado") is None:
        return "El significado está disponible; confirma la regla de código que corresponde."
    return None


def preparar(reporte, personas, dias, categorias, alertas, historia):
    """Una celda por vínculo y fecha; los problemas de leyenda se agrupan por código."""
    periodo = reporte["periodo"]
    fechas = sorted(
        {
            date(periodo.year, periodo.month, n)
            for n in range(1, calendar.monthrange(periodo.year, periodo.month)[1] + 1)
        }
        | {d["fecha"] for d in dias}
    )
    por_persona = {p["trabajador_en_reporte_id"]: p for p in personas}
    por_fecha = {}
    observaciones = []
    vacios = {}
    usados = codigos_usados_asistencia(dias)
    todas_categorias = []
    for indice, c in enumerate(categorias):
        ev = c.get("evidencia") or {}
        revision = c.get("revision_leyenda") or {}
        c["revision_id"] = f"codigo-{indice}"
        c["en_uso"] = c["codigo"] in usados
        c["problema"] = motivo_categoria(c) if c["en_uso"] else None
        c["fuente_url"] = fuente_url(
            reporte,
            celda=ev.get("celda") or revision.get("rango"),
            hoja=ev.get("hoja") or revision.get("hoja"),
        )
        c["localizador"] = " · ".join(
            str(v) for v in (ev.get("hoja"), ev.get("celda") or ev.get("pagina")) if v
        )
        c["rango_revision"] = revision.get("rango")
        c["par_revision"] = next(
            (p for p in revision.get("pares", []) if p.get("codigo") == c["codigo"]),
            None,
        )
        # Texto recibido utilizable por RRHH, sin volcar evidencia técnica/JSON.
        c["texto_fuente"] = (
            ev.get("descripcion") if isinstance(ev.get("descripcion"), str) else None
        )
        todas_categorias.append(c)
        if c["problema"]:
            observaciones.append(
                {
                    "id": c["revision_id"],
                    "tipo": "leyenda",
                    "titulo": f"Leyenda {c['codigo']}",
                    "detalle": c["problema"],
                    "categoria": c,
                    "fuente_url": c["fuente_url"],
                    "codigo": c["codigo"],
                }
            )
    categorias_pendientes = {c["codigo"] for c in categorias if c["problema"]}
    for d in dias:
        d["etiqueta"] = etiqueta_dia(d)
        d["clasificacion"] = categoria_dia(reporte, d)
        d["captura_texto"] = CAPTURAS.get(d["estado_captura"], d["estado_captura"])
        if d.get("evidencia_interpretacion", {}).get("no_correspondia_asistir"):
            d["captura_texto"] = "No correspondía asistir"
        if d["estado_captura"] == "VACIO":
            vacios.setdefault(d["fecha"], []).append(
                {
                    "id": d["asistencia_dia_id"],
                    "nombre": por_persona[d["trabajador_en_reporte_id"]][
                        "nombres_reportados_raw"
                    ],
                    "fila": por_persona[d["trabajador_en_reporte_id"]].get(
                        "fila_detalle_origen"
                    ),
                    "esperado": d.get("es_dia_laborable_esperado"),
                }
            )
        d["fuente_url"] = fuente_url(reporte, celda=d.get("celda_origen"))
        d["revision_id"] = f"dia-{d['asistencia_dia_id']}"
        d["requiere_lectura"] = d["estado_captura"] in {"ILEGIBLE", "PENDIENTE"} or (
            d["estado_captura"] == "VACIO"
            and d.get("es_dia_laborable_esperado") is not False
            and not d.get("validado")
        )
        d["requiere_leyenda"] = (
            d.get("codigo_interpretado") or d.get("codigo_reportado_raw")
        ) in categorias_pendientes
        por_fecha[(d["trabajador_en_reporte_id"], d["fecha"])] = d
        if d["requiere_lectura"] and d["estado_captura"] != "VACIO":
            p = por_persona[d["trabajador_en_reporte_id"]]
            observaciones.append(
                {
                    "id": d["revision_id"],
                    "tipo": "dia",
                    "titulo": f"{p['nombres_reportados_raw']} · {d['fecha']:%d/%m}",
                    "detalle": f"{d['captura_texto']}. Comprueba la fuente; un vacío no se interpreta como asistencia ni falta.",
                    "dia": d,
                    "persona": p,
                    "fuente_url": d["fuente_url"],
                }
            )
    for p in personas:
        p["celdas"] = [
            por_fecha.get((p["trabajador_en_reporte_id"], f)) for f in fechas
        ]
        if not p["trabajador_id"] or not p["rol_laboral_id"]:
            observaciones.append(
                {
                    "id": f"persona-{p['trabajador_en_reporte_id']}",
                    "tipo": "persona",
                    "titulo": f"Identidad · {p['nombres_reportados_raw']}",
                    "detalle": "Falta confirmar la identidad o el rol de esta fila. Consulta los vínculos registrados antes de asignarlos.",
                    "persona": p,
                    "fuente_url": fuente_url(
                        reporte, fila=p.get("fila_detalle_origen")
                    ),
                }
            )
    for a in alertas:
        if a["estado"] != "PENDIENTE":
            continue
        # El desfase se resuelve en los códigos, no mediante una decisión duplicada.
        if a["codigo_regla"] == "LEYENDA_POSIBLE_DESFASE":
            # Los códigos usados afectados ya tienen caso propio; los no usados
            # se conservan en la fuente, sin bloquear esta revisión mensual.
            continue
        d = por_fecha.get((a["trabajador_en_reporte_id"], a["fecha"]))
        observaciones.append(
            {
                "id": f"alerta-{a['validacion_reporte_id']}",
                "tipo": "alerta",
                "titulo": a["nombres_reportados_raw"] or "Observación del reporte",
                "detalle": a["explicacion"],
                "alerta": a,
                "dia": d,
                "fuente_url": d["fuente_url"]
                if d
                else fuente_url(reporte, fila=a.get("fila_detalle_origen")),
            }
        )
    if any(d["requiere_lectura"] and d["estado_captura"] == "VACIO" for d in dias):
        observaciones.append(
            {
                "id": "vacios-grupo",
                "tipo": "vacios",
                "titulo": f"Vacíos · {sum(len(v) for v in vacios.values())} celdas",
                "detalle": "Revisa los vacíos en grupo.",
            }
        )
    if not personas:
        observaciones.append(
            {
                "id": "sin-personal",
                "tipo": "informacion",
                "titulo": "Sin personal digitalizado",
                "detalle": "Esta extracción no contiene filas de personal. Comprueba el archivo y su carga.",
                "fuente_url": fuente_url(reporte),
            }
        )
    return {
        "fechas": fechas,
        "observaciones": observaciones,
        "categorias": todas_categorias,
        "historia": [evento_legible(e, por_persona) for e in historia],
        "fuente_inicial": fuente_url(reporte),
        "vacios": dict(sorted(vacios.items())),
        "total_vacios": sum(len(v) for v in vacios.values()),
    }


def evento_legible(evento, personas):
    nombres = {str(k): v["nombres_reportados_raw"] for k, v in personas.items()}
    antes, despues = evento["anterior"], evento["posterior"]
    accion = evento["accion"]
    cambios = []
    titulo = {
        "CORREGIR_MARCA": "Marca cotejada o corregida",
        "CLASIFICAR_LEYENDA": "Leyenda revisada",
        "REVISAR_REPORTE": "Digitalización revisada",
        "REVISAR_COHERENCIA": "Coherencia con calendario revisada",
        "AJUSTAR_ASISTENCIA_RRHH": "Asistencia ajustada por RRHH",
        "RESOLVER_IDENTIDAD": "Identidad y vínculo revisados",
        "CONFIRMAR_ALERTA": "Dato confirmado",
        "DESCARTAR_ALERTA": "Observación descartada",
        "DEJAR_PENDIENTE": "Observación conservada pendiente",
        "COMPROBAR_LEYENDA": "Comprobación de leyenda",
        "CLASIFICAR_VACIOS": "Vacíos revisados en grupo",
    }.get(accion, "Decisión registrada")
    if accion == "AJUSTAR_ASISTENCIA_RRHH":
        cambios.append(("Fecha", None, despues["fecha"]))
        cambios.append(
            (
                "Valor operativo",
                (antes.get("ajuste") or {}).get("etiqueta")
                or antes.get("categoria_declarada", {}).get("tipo_dia")
                or "No aplica / sin marca",
                despues["etiqueta"],
            )
        )
    elif accion == "REVISAR_COHERENCIA":
        from asistia.coherencia import DECISIONES

        cambios.append(("Casos", None, str(len(despues["casos"]))))
        cambios.append(("Fechas", None, ", ".join(despues["fechas"])))
        cambios.append(("Decisión", None, DECISIONES[despues["decision"]]))
    elif accion == "CLASIFICAR_VACIOS":
        dias = despues.get("dias", [])
        cambios.append(("Selección", f"{len(dias)} vacíos", "No correspondía asistir"))
        fechas = sorted({str(d["fecha"]) for d in dias})
        cambios.append(("Fechas", None, ", ".join(fechas)))
    elif accion == "CORREGIR_MARCA":
        cambios.append(("Fecha", antes.get("fecha"), despues.get("fecha")))
        cambios.append(("Marca", etiqueta_dia(antes), etiqueta_dia(despues)))
        cambios.append(
            (
                "Lectura",
                CAPTURAS.get(antes.get("estado_captura")),
                CAPTURAS.get(despues.get("estado_captura")),
            )
        )
    elif "clasificacion_codigos" in despues:
        for codigo, c in despues["clasificacion_codigos"].items():
            previo = antes.get("clasificacion_codigos", {}).get(codigo, {})
            if previo != c:
                cambios.append(
                    (
                        f"Código {codigo}",
                        previo.get("tipo_dia") or "Sin significado",
                        c.get("tipo_dia") or "Sin significado",
                    )
                )
                pago = lambda v: (
                    "Remunerado"
                    if v is True
                    else "No remunerado"
                    if v is False
                    else "Pendiente"
                )
                cambios.append(
                    (
                        "Remuneración",
                        pago(previo.get("es_remunerado")),
                        pago(c.get("es_remunerado")),
                    )
                )
    elif accion == "RESOLVER_IDENTIDAD":
        cambios.append(
            (
                "Identidad",
                "Sin asignar"
                if not antes.get("trabajador_id")
                else "Asignada previamente",
                "Vínculo cotejado por RRHH",
            )
        )
    return {
        **evento,
        "titulo": titulo,
        "persona": nombres.get(str(evento.get("trabajador_en_reporte_id"))),
        "cambios": cambios,
    }
