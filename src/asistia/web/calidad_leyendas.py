"""Bandeja y comprobación explícita de leyendas de reportes existentes."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import flash, g, redirect, render_template, request, url_for

from asistia.calidad_leyendas import cotejar_excel, pendiente, referencias
from asistia.leyendas import aplicar_leyenda_asistencia

from . import ErrorDeTrabajo
from .archivos import ruta_verificada
from .catalogo import documentos
from .db import base, escritura
from .lecturas import huella_reporte, reporte
from .revision import registrar, verificar_edicion


def hallazgos_categorias(categorias):
    grupos = {}
    for codigo, c in categorias.items():
        if not pendiente(c):
            continue
        h = c["revision_leyenda"]
        clave = (h["hoja"], h["rango"], h["regla"])
        grupos.setdefault(clave, {**h, "codigos_pendientes": []})[
            "codigos_pendientes"
        ].append(codigo)
    return list(grupos.values())


def listar(conn):
    return [
        dict(d, hallazgos=h)
        for d in documentos(conn, "asistencia")
        if (h := hallazgos_categorias(d["clasificacion_codigos"]))
    ]


def comprobar_reporte(conn, rid, uid, *, conocidas=None):
    """Solo suspende interpretaciones automáticas; revisión local y salidas se conservan.

    El llamador mantiene el bloqueo de escritura. La huella impide incorporar el
    diagnóstico a una versión que haya cambiado durante la lectura del original.
    """
    r = reporte(conn, rid)
    if Path(r["nombre_original"]).suffix.lower() not in {".xlsx", ".xlsm"}:
        return {"estado": "NO_APLICA", "reporte": str(rid)}
    huella = huella_reporte(conn, rid)
    path = ruta_verificada(r["ruta_objeto"], r["sha256"])
    antes = r["clasificacion_codigos"]
    despues = cotejar_excel(
        path,
        r["hoja_pagina_origen"],
        antes,
        referencias(conn) if conocidas is None else conocidas,
    )
    if despues == antes:
        return {"estado": "SIN_CAMBIOS", "reporte": str(rid)}
    verificar_edicion(conn, rid, huella)
    aplicar_leyenda_asistencia(conn, rid, despues, usar_catalogo=False)
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    registrar(
        conn,
        uuid.uuid4(),
        rid,
        "COMPROBAR_LEYENDA",
        "Control automático de alineación: posible desfase. Se conserva la lectura anterior y queda pendiente de revisión; no es aprobación RRHH.",
        {"clasificacion_codigos": antes, "estado": r["estado"]},
        {
            "clasificacion_codigos": despues,
            "sha256": r["sha256"],
            "estado": "EN_VALIDACION",
        },
        uid,
    )
    return {
        "estado": "DETECTADO",
        "reporte": str(rid),
        "hallazgos": hallazgos_categorias(despues),
    }


def registrar_rutas(pages):
    @pages.get("/monitoreo/leyendas")
    def monitoreo_leyendas():
        rows = listar(base())
        mes, nivel = request.args.get("mes", ""), request.args.get("nivel_filtro", "")
        return render_template(
            "monitoreo_leyendas.html",
            reportes=[
                r
                for r in rows
                if (not mes or r["periodo"][:7] == mes)
                and (not nivel or r["nivel"] == nivel)
            ],
            total=len(rows),
            mes=mes,
            nivel_filtro=nivel,
        )

    @pages.post("/monitoreo/leyendas/comprobar")
    def monitoreo_leyendas_comprobar():
        resultados, errores = [], []
        with escritura() as conn:
            conocidas = referencias(conn)
            for d in documentos(conn, "asistencia"):
                try:
                    with conn.transaction():
                        resultados.append(
                            comprobar_reporte(
                                conn,
                                d["id"],
                                g.usuario["usuario_id"],
                                conocidas=conocidas,
                            )
                        )
                except (ErrorDeTrabajo, ValueError, OSError, KeyError):
                    errores.append(d)
        flash(
            f"Comprobación terminada: {sum(r['estado'] != 'NO_APLICA' for r in resultados)} reportes Excel comprobados; "
            f"{sum(r['estado'] == 'DETECTADO' for r in resultados)} reportes con nuevas observaciones. "
            f"{len(errores)} reportes no pudieron comprobarse. Las versiones y salidas conservadas mantienen sus valores."
        )
        for d in errores:
            flash(
                f"No se pudo comprobar el reporte {d['id']}: {d['nombre_ie']}. Abre su original y revisa la hoja o disponibilidad del archivo.",
                "error",
            )
        return redirect(url_for("pages.monitoreo_leyendas"), code=303)
