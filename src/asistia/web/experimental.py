"""Consulta de perfiles sintéticos, reasignación de meses e Isolation Forest."""

import io

from flask import (
    Response,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from asistia.experimental.datos import CAMPOS, leer_corpus, mes, publicar, redefinir
from asistia.experimental.modelo import ejecutar
from asistia.experimental.monitoreo import VARIABLES, experimento_lote
from asistia.experimental.salidas import csv_reportes, paquete_zip, reportes_lote
from asistia.niveles import NIVELES

from . import ErrorDeTrabajo
from .db import base, escritura


def lote_existente(lote_id):
    lote = (
        base()
        .execute("SELECT * FROM ml_lote WHERE ml_lote_id=%s", (lote_id,))
        .fetchone()
    )
    if not lote:
        raise ErrorDeTrabajo(
            "El lote solicitado no existe. Vuelve a Perfiles experimentales.", 404
        )
    return lote


def registrar_rutas(pages):
    @pages.get("/experimental")
    def experimental():
        lotes = (
            base()
            .execute("""SELECT l.*,
            (SELECT count(*) FROM ml_reporte_mensual r WHERE r.ml_lote_id=l.ml_lote_id) AS reportes
            FROM ml_lote l ORDER BY creado_en DESC LIMIT 50""")
            .fetchall()
        )
        return render_template("experimental.html", lotes=lotes)

    @pages.post("/experimental/importar")
    def experimental_importar():
        try:
            with escritura() as conn:
                reportes, manifiesto = leer_corpus(
                    conn,
                    current_app.config.get(
                        "EXPERIMENTAL_DRE_ROOT",
                        "data/raw/data_brindada_por_ugel/asistencia_marzo_2026_a_junio_2026",
                    ),
                )
                lid, nuevo = publicar(
                    conn,
                    reportes,
                    manifiesto,
                    inicio=request.form.get("inicio") or None,
                    autor=str(g.usuario["usuario_id"]),
                    motivo=request.form.get("motivo", ""),
                )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        flash(
            "Lote sintético creado."
            if nuevo
            else "Este contenido ya está cargado. Se abrió el lote existente."
        )
        return redirect(url_for("pages.experimental_lote", lote_id=lid), code=303)

    @pages.get("/experimental/lotes/<uuid:lote_id>")
    def experimental_lote(lote_id):
        lote = lote_existente(lote_id)
        reportes = reportes_lote(base(), lote_id)
        meses = sorted({str(r["periodo"])[:7] for r in reportes})
        seleccionado = request.args.get("mes", meses[0] if meses else "")
        nivel = request.args.get("nivel_filtro", "TODOS")
        if seleccionado not in meses or nivel not in {"TODOS", *NIVELES}:
            raise ErrorDeTrabajo(
                "Selecciona uno de los meses y niveles disponibles en el lote."
            )
        experimento = (
            base()
            .execute(
                "SELECT ml_experimento_id,manifiesto,resultados FROM ml_experimento WHERE ml_lote_id=%s ORDER BY creado_en DESC LIMIT 1",
                (lote_id,),
            )
            .fetchone()
        )
        puntuaciones = (
            {r["reporte_id"]: r for r in experimento["resultados"]}
            if experimento
            else {}
        )
        visibles = [
            r
            for r in reportes
            if str(r["periodo"])[:7] == seleccionado
            and (nivel == "TODOS" or r["nivel"] == nivel)
        ]
        for r in visibles:
            r["puntuacion"] = puntuaciones.get(str(r["ml_reporte_mensual_id"]))
        visibles.sort(
            key=lambda r: (
                r["puntuacion"]["puesto"] if r["puntuacion"] else 999999,
                r["cod_mod"],
                r["anexo"],
            )
        )
        try:
            pagina = max(1, int(request.args.get("pagina", 1)))
        except ValueError:
            raise ErrorDeTrabajo("La página solicitada no es válida.") from None
        n = len(visibles)
        return render_template(
            "experimental_lote.html",
            lote=lote,
            reportes=visibles[(pagina - 1) * 40 : pagina * 40],
            visibles=n,
            pagina=pagina,
            meses=meses,
            mes_elegido=seleccionado,
            nivel_filtro=nivel,
            experimento=experimento,
            total=len(reportes),
            aptos=sum(r["datos"]["apto_modelo"] for r in reportes),
        )

    @pages.post("/experimental/lotes/<uuid:lote_id>/periodos")
    def experimental_periodos(lote_id):
        lote_existente(lote_id)
        try:
            inicio = request.form.get("inicio", "")
            mes(inicio)
            with escritura() as conn:
                lid, nuevo = redefinir(
                    conn,
                    lote_id,
                    inicio,
                    autor=str(g.usuario["usuario_id"]),
                    motivo=request.form.get("motivo", ""),
                )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        flash(
            "Nueva versión con períodos redefinidos."
            if nuevo
            else "Esta reasignación ya existe. Se abrió la versión conservada."
        )
        return redirect(url_for("pages.experimental_lote", lote_id=lid), code=303)

    @pages.post("/experimental/lotes/<uuid:lote_id>/entrenar")
    def experimental_entrenar(lote_id):
        lote_existente(lote_id)
        try:
            with escritura() as conn:
                _, nuevo = ejecutar(conn, lote_id, autor=str(g.usuario["usuario_id"]))
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        flash(
            "Experimento terminado. Revisa resultados y límites."
            if nuevo
            else "Ya existe este experimento con la misma configuración; se conservaron sus resultados."
        )
        return redirect(url_for("pages.experimental_lote", lote_id=lote_id), code=303)

    @pages.get("/monitoreo/reportes/<uuid:reporte_id>", endpoint="monitoreo_reporte")
    @pages.get("/experimental/reportes/<uuid:reporte_id>")
    def experimental_reporte(reporte_id):
        r = (
            base()
            .execute(
                """SELECT r.*,ie.cod_mod,ie.anexo,ie.nombre_ie FROM ml_reporte_mensual r
            JOIN institucion_educativa ie USING(institucion_educativa_id) WHERE ml_reporte_mensual_id=%s""",
                (reporte_id,),
            )
            .fetchone()
        )
        if not r:
            raise ErrorDeTrabajo("El reporte solicitado no existe.", 404)
        lote = lote_existente(r["ml_lote_id"])
        desde_monitor = request.endpoint == "pages.monitoreo_reporte"
        try:
            exp = experimento_lote(
                base(),
                r["ml_lote_id"],
                request.args.get("experimento") if desde_monitor else None,
            )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        volver_monitor = None
        if desde_monitor:
            volver_monitor = url_for(
                "pages.monitoreo",
                lote=r["ml_lote_id"],
                experimento=exp["ml_experimento_id"] if exp else None,
                mes=request.args.get("mes", str(r["periodo"])[:7]),
                nivel_filtro=request.args.get("nivel_filtro", "TODOS"),
                estado=request.args.get("estado", "TODOS"),
                q=request.args.get("q", ""),
                pagina=request.args.get("pagina", 1),
            )
        score = (
            next(
                (s for s in exp["resultados"] if s["reporte_id"] == str(reporte_id)),
                None,
            )
            if exp
            else None
        )
        return render_template(
            "experimental_reporte.html",
            reporte=r,
            lote=lote,
            campos=CAMPOS,
            score=score,
            volver_monitor=volver_monitor,
            experimento_monitor=exp if desde_monitor else None,
            variables_monitor=VARIABLES,
        )

    @pages.get("/experimental/lotes/<uuid:lote_id>/reportes.csv")
    def experimental_csv(lote_id):
        lote_existente(lote_id)
        exp = (
            base()
            .execute(
                "SELECT resultados FROM ml_experimento WHERE ml_lote_id=%s ORDER BY creado_en DESC LIMIT 1",
                (lote_id,),
            )
            .fetchone()
        )
        return Response(
            csv_reportes(
                reportes_lote(base(), lote_id), exp["resultados"] if exp else None
            ),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=reportes_mensuales_SINTETICOS.csv"
            },
        )

    @pages.get("/experimental/experimentos/<uuid:experimento_id>/paquete.zip")
    def experimental_paquete(experimento_id):
        try:
            contenido = paquete_zip(base(), experimento_id)
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc), 404) from None
        return send_file(
            io.BytesIO(contenido),
            mimetype="application/zip",
            as_attachment=True,
            download_name="ASISTIA_EXPERIMENTO_SINTETICO.zip",
        )
