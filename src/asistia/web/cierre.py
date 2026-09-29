"""Pantallas del expediente de cierre: fuentes, excepciones y revisión técnica."""

import csv
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Response,
    current_app,
    flash,
    g,
    redirect,
    request,
    send_file,
    url_for,
)
from psycopg.types.json import Jsonb

from asistia.cierre.gemini import Gemini, SinCupo
from asistia.cierre.ingesta import asignar_contexto, estado, procesar_documento

from . import ErrorDeTrabajo
from .archivos import ruta_verificada
from .db import base, escritura
from .lecturas import uno

ESTADOS = {
    "PENDIENTE": "Pendiente de extracción",
    "PROCESANDO": "Procesando",
    "PROCESADO": "Datos cargados",
    "PARCIAL": "Carga parcial",
    "ERROR": "Requiere reintento",
    "ESPERANDO_CUPO": "En espera de cuota OCR",
    "SIN_DATOS": "Sin detalle utilizable",
    "NO_NECESARIO": "Archivo auxiliar o no necesario",
}
TIPOS = {
    "NEXUS": "NEXUS",
    "DRE": "Padrón desde DRE",
    "ASISTENCIA": "Asistencia",
    "CALENDARIO_2026": "Calendario 2026",
    "CALENDARIO_2025": "Fuente de calendario 2025",
}


def registrar_rutas(pages):
    from .routes import ambito, operacion, vista

    @pages.get("/cierre")
    def cierre():
        periodo, nivel = ambito()
        conn = base()
        resumen = conn.execute(
            "SELECT estado,count(*) AS n FROM cierre_documento GROUP BY estado"
        ).fetchall()
        filtro = request.args.get("filtro", "pendientes")
        if filtro not in ("todos", "pendientes", "cargados"):
            raise ErrorDeTrabajo("Filtro de documentos no válido")
        try:
            pagina = max(1, int(request.args.get("pagina", 1)))
        except ValueError:
            raise ErrorDeTrabajo("Página no válida") from None
        condicion = {
            "todos": "TRUE",
            "pendientes": "estado NOT IN ('PROCESADO','NO_NECESARIO')",
            "cargados": "estado='PROCESADO'",
        }[filtro]
        total = conn.execute(
            f"SELECT count(*) AS n FROM cierre_documento WHERE {condicion}"
        ).fetchone()["n"]
        documentos = conn.execute(
            f"SELECT * FROM cierre_documento WHERE {condicion} ORDER BY tipo,ruta LIMIT 40 OFFSET %s",
            ((pagina - 1) * 40,),
        ).fetchall()
        for d in documentos:
            d["nombre"] = Path(d["ruta"]).name
        instituciones = conn.execute(
            """SELECT ie.*,r.resultado,r.detalle,r.revisado_en
          FROM institucion_educativa ie LEFT JOIN LATERAL
           (SELECT * FROM cierre_revision_institucion r WHERE r.institucion_educativa_id=ie.institucion_educativa_id
             AND r.periodo=%s ORDER BY revisado_en DESC LIMIT 1) r ON true
          WHERE fn_nivel_canonico(ie.nivel_modalidad)=%s ORDER BY ie.nombre_ie""",
            (periodo, nivel),
        ).fetchall()
        return vista(
            "cierre.html",
            resumen={r["estado"]: r["n"] for r in resumen},
            documentos=documentos,
            instituciones=instituciones,
            etiquetas=ESTADOS,
            tipos=TIPOS,
            filtro=filtro,
            pagina=pagina,
            total=total,
        )

    @pages.get("/cierre/documentos/<uuid:did>")
    def documento_cierre(did):
        conn = base()
        d = uno(
            conn, "SELECT * FROM cierre_documento WHERE cierre_documento_id=%s", (did,)
        )
        d["nombre"] = Path(d["ruta"]).name
        reportes = conn.execute(
            """SELECT r.reporte_asistencia_id,r.periodo,ie.nombre_ie FROM reporte_asistencia r
          JOIN documento_recibido dr USING(documento_recibido_id) JOIN objeto_archivo o USING(objeto_archivo_id)
          JOIN institucion_educativa ie USING(institucion_educativa_id) WHERE o.sha256=%s ORDER BY r.periodo,r.version""",
            (d["sha256"],),
        ).fetchall()
        calendarios = conn.execute(
            """SELECT cv.calendarizacion_version_id,cl.anio,ie.nombre_ie FROM calendarizacion_version cv
          JOIN documento_recibido dr USING(documento_recibido_id) JOIN objeto_archivo o USING(objeto_archivo_id)
          JOIN calendarizacion_local cl USING(calendarizacion_local_id) JOIN institucion_educativa ie USING(institucion_educativa_id)
          WHERE o.sha256=%s ORDER BY cl.anio,cv.version""",
            (d["sha256"],),
        ).fetchall()
        padron = conn.execute(
            "SELECT * FROM padron_evidencia WHERE cierre_documento_id=%s ORDER BY hoja,fila",
            (did,),
        ).fetchall()
        return vista(
            "cierre_documento.html",
            doc=d,
            etiquetas=ESTADOS,
            tipos=TIPOS,
            reportes=reportes,
            calendarios=calendarios,
            padron=padron,
            opciones_ie=conn.execute(
                "SELECT institucion_educativa_id,nombre_ie,cod_mod,nivel_modalidad FROM institucion_educativa ORDER BY nombre_ie,cod_mod"
            ).fetchall(),
        )

    @pages.post("/cierre/preparar-salida")
    def preparar_cierre():
        from asistia.cierre.revision import revisar_instituciones
        from asistia.cierre.salidas import alcance_nivel

        from .salidas import preparar

        periodo, nivel = ambito()
        sid = operacion()
        with escritura() as conn:
            revisar_instituciones(
                conn,
                periodo,
                evidencia_dir=current_app.config["STORAGE_ROOT"] / "cierre",
            )
            alcance = alcance_nivel(conn, periodo, nivel)
            preparar(
                conn,
                periodo,
                nivel,
                False,
                sid,
                g.usuario["usuario_id"],
                cierre_tecnico=alcance,
            )
        flash(
            "Borrador técnico preparado con fuentes, supuestos y pendientes conservados. Requiere cotejo de RRHH."
        )
        return redirect(url_for("pages.salida", sid=sid), 303)

    @pages.post("/cierre/documentos/<uuid:did>/institucion")
    def asignar_institucion_cierre(did):
        try:
            bloque = int(request.form.get("bloque", ""))
            iid = int(request.form.get("institucion", ""))
        except ValueError:
            raise ErrorDeTrabajo("Selecciona la institución del bloque.") from None
        with escritura() as conn:
            try:
                asignar_contexto(
                    conn,
                    did,
                    bloque,
                    iid,
                    request.form.get("nivel_bloque", ""),
                    request.form.get("motivo", "").strip(),
                    f"USUARIO:{g.usuario['usuario_id']}",
                )
            except ValueError as exc:
                raise ErrorDeTrabajo(str(exc), 409) from None
        flash(
            "Asignación guardada con su motivo. Reintenta la extracción para aplicar el bloque conservado."
        )
        return redirect(url_for("pages.documento_cierre", did=did), 303)

    @pages.get("/cierre/documentos/<uuid:did>/original")
    def original_cierre(did):
        d = uno(
            base(),
            "SELECT * FROM cierre_documento WHERE cierre_documento_id=%s",
            (did,),
        )
        ruta = ruta_verificada(d["ruta"], d["sha256"])
        return send_file(ruta, as_attachment=True, download_name=Path(d["ruta"]).name)

    @pages.post("/cierre/documentos/<uuid:did>/reintentar")
    def reintentar_cierre(did):
        conn = base()
        d = uno(
            conn, "SELECT * FROM cierre_documento WHERE cierre_documento_id=%s", (did,)
        )
        if d["estado"] in {"PROCESADO", "NO_NECESARIO"}:
            flash("El documento ya quedó procesado. Sus resultados se conservan.")
            return redirect(url_for("pages.documento_cierre", did=did), 303)
        if not conn.execute("SELECT pg_try_advisory_lock(26091206) AS ok").fetchone()[
            "ok"
        ]:
            raise ErrorDeTrabajo(
                "La carga masiva está en curso. Actualiza el estado antes de reintentar.",
                409,
            )
        try:
            if request.form.get("nueva_lectura") == "1":
                contexto = dict(d["contexto_tecnico"])
                contexto["relectura_ocr"] = str(uuid.uuid4())
                contexto.setdefault("historial", []).append(
                    {
                        "accion": "RELECTURA_OCR",
                        "autor": f"USUARIO:{g.usuario['usuario_id']}",
                        "fecha": datetime.now(UTC).isoformat(),
                        "motivo": "Revisar extracción parcial o sin detalle utilizable.",
                    }
                )
                conn.execute(
                    "UPDATE cierre_documento SET contexto_tecnico=%s WHERE cierre_documento_id=%s",
                    (Jsonb(contexto), did),
                )
                conn.commit()
                d["contexto_tecnico"] = contexto
            procesar_documento(conn, d, Gemini())
            flash(
                "Procesamiento terminado. Comprueba los resultados y pendientes del documento."
            )
        except SinCupo as exc:
            conn.rollback()
            estado(conn, d, "ESPERANDO_CUPO", error=str(exc), metodo="GEMINI")
            flash(
                "El original está conservado. La cuota de extracción está agotada temporalmente; reintenta cuando se restablezca."
            )
        except Exception as exc:  # noqa: BLE001 -- conservar estado de intento y evitar SQL/secretos en UI
            conn.rollback()
            mensaje = (
                str(exc)[:500]
                if isinstance(exc, (ValueError, TypeError))
                else type(exc).__name__
            )
            estado(conn, d, "ERROR", error=mensaje)
            flash(
                "No se completó la extracción. El original se conserva; revisa su contenido antes de reintentar.",
                "error",
            )
        finally:
            conn.execute("SELECT pg_advisory_unlock(26091206)")
            conn.commit()
        return redirect(url_for("pages.documento_cierre", did=did), 303)

    @pages.get("/cierre/instituciones.csv")
    def cierre_csv():
        periodo, nivel = ambito()
        filas = (
            base()
            .execute(
                """SELECT ie.cod_mod,ie.anexo,ie.nombre_ie,ie.nivel_modalidad,r.resultado,r.detalle->>'siguiente_accion' AS accion
          FROM institucion_educativa ie LEFT JOIN LATERAL(SELECT * FROM cierre_revision_institucion r
          WHERE r.institucion_educativa_id=ie.institucion_educativa_id AND r.periodo=%s ORDER BY revisado_en DESC LIMIT 1) r ON true
          WHERE fn_nivel_canonico(ie.nivel_modalidad)=%s ORDER BY ie.cod_mod""",
                (periodo, nivel),
            )
            .fetchall()
        )
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            [
                "Código modular",
                "Anexo",
                "Institución",
                "Nivel",
                "Revisión técnica",
                "Acción pendiente",
            ]
        )
        for f in filas:
            w.writerow(
                [
                    ("'" + str(v))
                    if str(v or "").startswith(("=", "+", "-", "@"))
                    else v
                    for v in f.values()
                ]
            )
        return Response(
            "\ufeff" + out.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="revision_tecnica_{periodo}_{nivel}.csv"'
            },
        )
