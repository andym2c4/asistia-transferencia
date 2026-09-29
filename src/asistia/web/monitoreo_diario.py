"""Monitoreo de actividad diaria, evidencia conservada y entrenamiento explícito."""

import io

from flask import flash, g, redirect, render_template, request, send_file, url_for

from asistia.experimental.monitoreo import ESTADOS, identificador, preparar_panel
from asistia.monitoreo.cortes import crear, perfiles_corte
from asistia.monitoreo.modelo import METODOS
from asistia.monitoreo.reglas import REGLAS, VARIABLES

from . import ErrorDeTrabajo
from .db import base, escritura

ESTADOS_DIARIOS = ESTADOS | {
    "CON_ALERTAS": "Con alertas de reglas",
    "CON_PENDIENTES": "Con cruces pendientes",
}
MOTIVOS = {
    "CALENDARIO_SIN_CONFIRMAR": "Falta confirmar la calendarización del año",
    "LEYENDA_POR_REVISAR": "Posible desfase de leyenda: revisar códigos y descripciones",
    "IDENTIDAD_O_ROL_PENDIENTE": "Identidad o rol por resolver",
    "VIGENCIA_PENDIENTE": "Vínculo o vigencia por resolver",
    "SIN_VIGENCIA_ESPERADA": "Sin presencia esperada por vigencia del vínculo",
    "FUENTES_DIARIAS_CONTRADICTORIAS": "Las fuentes diarias se contradicen",
    "SIN_CALENDARIO": "Falta calendario institucional",
    "CALENDARIO_DERIVADO_2025": "Calendario estimado desde 2025",
    "CATEGORIA_CALENDARIO_PENDIENTE": "Categoría de calendario sin actividad definida",
    "CATEGORIA_ASISTENCIA_PENDIENTE": "Declaración diaria sin interpretación suficiente",
}


def corte(cid):
    r = (
        base()
        .execute(
            "SELECT corte_id,huella,anio,naturaleza,manifiesto,resultados,autor,motivo,creado_en "
            "FROM monitoreo_diario_corte WHERE corte_id=%s",
            (cid,),
        )
        .fetchone()
    )
    if not r:
        raise ErrorDeTrabajo(
            "El corte no existe. Vuelve a Monitoreo para elegir otra versión.", 404
        )
    return r


def registrar_rutas(pages):
    @pages.get("/monitoreo/diario")
    def monitoreo_diario():
        conn = base()
        cortes = conn.execute(
            "SELECT corte_id,anio,motivo,creado_en FROM monitoreo_diario_corte ORDER BY creado_en DESC,corte_id DESC"
        ).fetchall()
        anios = conn.execute(
            "SELECT DISTINCT extract(year from periodo)::int AS anio FROM reporte_asistencia ORDER BY anio DESC"
        ).fetchall()
        if not cortes and not request.args.get("corte"):
            return render_template(
                "monitoreo_diario.html", corte=None, cortes=cortes, anios=anios
            )
        try:
            c = corte(identificador(request.args.get("corte") or cortes[0]["corte_id"]))
            filas = perfiles_corte(conn, c["corte_id"])
            reportes = [
                {
                    **r,
                    **r["identidad"],
                    "ml_reporte_mensual_id": r["perfil_id"],
                    "periodo_fuente": r["periodo"],
                }
                for r in filas
            ]
            evaluacion = c["manifiesto"]["evaluacion"]
            exp = (
                {"resultados": c["resultados"], "manifiesto": evaluacion}
                if evaluacion["estado"] == "ENTRENADO"
                else None
            )
            # Último mes con mayor población para abrir el corpus representativo,
            # sin presentar el pequeño lote de agosto como el conjunto completo.
            por_mes = {str(r["periodo"])[:7] for r in filas}
            inicial = (
                max(
                    por_mes,
                    key=lambda m: (sum(str(r["periodo"])[:7] == m for r in filas), m),
                )
                if por_mes
                else None
            )
            panel = preparar_panel(
                reportes,
                exp,
                mes=request.args.get("mes") or inicial,
                nivel=request.args.get("nivel_filtro", "TODOS"),
                estado=request.args.get("estado", "CON_ALERTAS"),
                q=request.args.get("q", ""),
                pagina=request.args.get("pagina", 1),
                estados_adicionales={
                    "CON_ALERTAS": lambda r: r["datos"]["dias_con_alerta"] > 0,
                    "CON_PENDIENTES": lambda r: bool(
                        r["datos"]["pendientes"] or r["datos"]["exclusiones"]
                    ),
                },
            )
        except ValueError as e:
            raise ErrorDeTrabajo(str(e)) from None
        ambito = [
            r
            for r in filas
            if str(r["periodo"])[:7] == panel["mes_elegido"]
            and (
                panel["nivel_filtro"] == "TODOS"
                or r["identidad"]["nivel"] == panel["nivel_filtro"]
            )
        ]
        reglas = c["manifiesto"].get("reglas", REGLAS)
        panel["resumen"].update(
            con_alertas=sum(r["datos"]["dias_con_alerta"] > 0 for r in ambito),
            dias_con_alerta=sum(r["datos"]["dias_con_alerta"] for r in ambito),
            reglas={
                regla: sum(
                    r["datos"]["alertas_por_regla"].get(regla, 0) for r in ambito
                )
                for regla in reglas
            },
        )
        return render_template(
            "monitoreo_diario.html",
            corte=c,
            cortes=cortes,
            anios=anios,
            evaluacion=evaluacion,
            estados_diarios=ESTADOS_DIARIOS,
            reglas=reglas,
            variables=c["manifiesto"].get("variables", VARIABLES),
            metodos=METODOS,
            **panel,
        )

    @pages.post("/monitoreo/diario/actualizar")
    def monitoreo_diario_actualizar():
        try:
            anio = int(request.form.get("anio", ""))
            with escritura() as conn:
                cid, nuevo = crear(
                    conn,
                    anio,
                    autor=str(g.usuario["usuario_id"]),
                    motivo=request.form.get("motivo", ""),
                )
        except ValueError as e:
            raise ErrorDeTrabajo("Revisa el año y el motivo. " + str(e)) from None
        flash(
            "Corte conservado. Consulta alertas, cobertura y estado del modelo."
            if nuevo
            else "Las fuentes, reglas y modelo ya corresponden a este corte; se abrió la versión conservada."
        )
        return redirect(url_for("pages.monitoreo_diario", corte=cid), code=303)

    @pages.get("/monitoreo/diario/perfiles/<uuid:perfil_id>")
    def monitoreo_diario_perfil(perfil_id):
        p = (
            base()
            .execute(
                "SELECT * FROM monitoreo_diario_perfil WHERE perfil_id=%s", (perfil_id,)
            )
            .fetchone()
        )
        if not p:
            raise ErrorDeTrabajo("El perfil no existe. Vuelve a Monitoreo.", 404)
        c = corte(p["corte_id"])
        reglas = c["manifiesto"].get("reglas", REGLAS)
        mostrar = request.args.get("mostrar", "ALERTAS")
        regla = request.args.get("regla", "TODAS")
        if mostrar not in {"ALERTAS", "PENDIENTES", "TODOS"} or regla not in {
            "TODAS",
            *reglas,
        }:
            raise ErrorDeTrabajo("Selecciona un estado diario y una regla disponibles.")
        try:
            pagina = max(1, int(request.args.get("dia_pagina", 1)))
        except ValueError:
            raise ErrorDeTrabajo("La página debe ser un número entero.") from None
        dias = [
            d
            for d in p["datos"]["dias"]
            if (
                mostrar == "TODOS"
                or (mostrar == "ALERTAS" and d["alertas"])
                or (mostrar == "PENDIENTES" and (d["pendiente"] or d["exclusion"]))
            )
            and (regla == "TODAS" or regla in d["alertas"])
        ]
        dias.sort(key=lambda d: (d["fecha"], d["clave"]))
        paginas = max(1, (len(dias) + 39) // 40)
        pagina = min(pagina, paginas)
        contexto = {
            k: request.args.get(k, defecto)
            for k, defecto in {
                "mes": str(p["periodo"])[:7],
                "nivel_filtro": "TODOS",
                "estado": "CON_ALERTAS",
                "q": "",
                "pagina": "1",
            }.items()
        }
        score = next(
            (r for r in c["resultados"] if r["reporte_id"] == str(perfil_id)), None
        )
        return render_template(
            "monitoreo_diario_perfil.html",
            perfil=p,
            corte=c,
            score=score,
            dias=dias[(pagina - 1) * 40 : pagina * 40],
            total_dias=len(dias),
            dia_pagina=pagina,
            dia_paginas=paginas,
            mostrar=mostrar,
            regla_elegida=regla,
            contexto=contexto,
            reglas=reglas,
            variables=c["manifiesto"].get("variables", VARIABLES),
            motivos=MOTIVOS,
        )

    @pages.get("/monitoreo/diario/cortes/<uuid:corte_id>/paquete.zip")
    def monitoreo_diario_paquete(corte_id):
        r = (
            base()
            .execute(
                "SELECT paquete FROM monitoreo_diario_corte WHERE corte_id=%s",
                (corte_id,),
            )
            .fetchone()
        )
        if not r or r["paquete"] is None:
            raise ErrorDeTrabajo("El expediente del corte no está disponible.", 404)
        return send_file(
            io.BytesIO(bytes(r["paquete"])),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"ASISTIA_MONITOREO_DIARIO_{corte_id}.zip",
        )
