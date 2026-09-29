"""Panel de monitoreo sobre experimentos conservados y autenticación RRHH común."""

from flask import redirect, render_template, request, url_for

from asistia.experimental.monitoreo import (
    ESTADOS,
    VARIABLES,
    experimento_lote,
    identificador,
    preparar_panel,
)
from asistia.experimental.salidas import reportes_lote

from . import ErrorDeTrabajo
from .db import base


def registrar_rutas(pages):
    @pages.get("/monitoreo")
    def monitoreo():
        conn = base()
        if (
            not request.args
            and conn.execute("SELECT 1 FROM monitoreo_diario_corte LIMIT 1").fetchone()
        ):
            return redirect(url_for("pages.monitoreo_diario"))
        lotes = conn.execute(
            "SELECT ml_lote_id,creado_en,motivo FROM ml_lote ORDER BY creado_en DESC,ml_lote_id DESC"
        ).fetchall()
        if not lotes and not request.args.get("lote"):
            return render_template("monitoreo.html", lote=None, lotes=lotes)
        try:
            lid = identificador(request.args.get("lote") or lotes[0]["ml_lote_id"])
            lote = conn.execute(
                "SELECT * FROM ml_lote WHERE ml_lote_id=%s", (lid,)
            ).fetchone()
            if not lote:
                raise ErrorDeTrabajo(
                    "El lote solicitado no existe. Vuelve a Monitoreo para elegir otra versión.",
                    404,
                )
            experimento = experimento_lote(conn, lid, request.args.get("experimento"))
            opciones = conn.execute(
                "SELECT ml_experimento_id,creado_en,manifiesto->'configuracion'->>'version' AS version "
                "FROM ml_experimento WHERE ml_lote_id=%s ORDER BY creado_en DESC,ml_experimento_id DESC",
                (lid,),
            ).fetchall()
            panel = preparar_panel(
                reportes_lote(conn, lid),
                experimento,
                mes=request.args.get("mes"),
                nivel=request.args.get("nivel_filtro", "TODOS"),
                estado=request.args.get("estado"),
                q=request.args.get("q", ""),
                pagina=request.args.get("pagina", 1),
            )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        return render_template(
            "monitoreo.html",
            lote=lote,
            lotes=lotes,
            experimento=experimento,
            experimentos=opciones,
            estados_monitor=ESTADOS,
            variables_monitor=VARIABLES,
            **panel,
        )
