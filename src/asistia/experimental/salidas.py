"""Expediente descargable: perfiles, componentes, modelo y límites desde el mismo corte."""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import openpyxl

from .datos import CAMPOS


def reportes_lote(conn, lote_id):
    return conn.execute(
        """SELECT r.*,ie.cod_mod,ie.anexo,ie.nombre_ie,
         fn_nivel_canonico(ie.nivel_modalidad) AS nivel
      FROM ml_reporte_mensual r JOIN institucion_educativa ie USING(institucion_educativa_id)
      WHERE ml_lote_id=%s ORDER BY periodo,ie.cod_mod,ie.anexo""",
        (lote_id,),
    ).fetchall()


def filas_exportacion(reportes, resultados=None):
    resultados = {r["reporte_id"]: r for r in resultados or []}
    for r in reportes:
        score = resultados.get(str(r["ml_reporte_mensual_id"]), {})
        fila = {
            "naturaleza": "SINTETICO_DERIVADO_DRE",
            "reporte_id": str(r["ml_reporte_mensual_id"]),
            "cod_mod": r["cod_mod"],
            "anexo": r["anexo"],
            "institucion": r["nombre_ie"],
            "nivel": r["nivel"],
            "periodo_fuente": str(r["periodo_fuente"])[:7],
            "periodo_destino": str(r["periodo"])[:7],
            "registros_laborales": r["datos"]["registros_laborales"],
            "apto_modelo": r["datos"]["apto_modelo"],
        }
        for campo in CAMPOS:
            d = r["datos"]["componentes"][campo]
            fila.update(
                {
                    f"{campo}_{nombre}": d[nombre]
                    for nombre in (
                        "suma_observada_valida",
                        "celdas_observadas_validas",
                        "suma_utilizada",
                        "registros_conocidos",
                        "registros_desconocidos",
                    )
                }
            )
        fila.update(r["datos"]["features"])
        fila.update(
            {
                k: score.get(k, "NO_PUNTUADO")
                for k in (
                    "particion",
                    "score_if",
                    "score_referencia",
                    "puesto",
                    "en_cupo",
                )
            }
        )
        yield fila


def seguro(valor):
    return (
        "'" + valor
        if isinstance(valor, str)
        and valor.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
        else valor
    )


def csv_reportes(reportes, resultados=None):
    filas = list(filas_exportacion(reportes, resultados))
    if not filas:
        return "\ufeffnaturaleza,periodo_destino\n"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(filas[0]))
    writer.writeheader()
    writer.writerows({k: seguro(v) for k, v in f.items()} for f in filas)
    return "\ufeff" + buffer.getvalue()


def paquete_zip(conn, experimento_id):
    exp = conn.execute(
        "SELECT * FROM ml_experimento WHERE ml_experimento_id=%s", (experimento_id,)
    ).fetchone()
    if not exp:
        raise ValueError("El experimento solicitado no existe.")
    if exp["paquete"] is not None:
        return bytes(exp["paquete"])
    lote = conn.execute(
        "SELECT * FROM ml_lote WHERE ml_lote_id=%s", (exp["ml_lote_id"],)
    ).fetchone()
    reportes = reportes_lote(conn, exp["ml_lote_id"])
    contenido = {
        "perfiles_sinteticos.csv": csv_reportes(reportes, exp["resultados"]).encode(
            "utf-8"
        ),
        "lote_y_procedencia.json": json.dumps(
            {"lote": lote, "reportes": reportes},
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode(),
        "experimento.json": json.dumps(
            {k: v for k, v in exp.items() if k not in {"modelo", "paquete"}},
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode(),
        "modelo.joblib": bytes(exp["modelo"]),
        "LEEME.md": (
            "# ASISTIA — experimento sintético mensual\n\n"
            "Reportes SINTETICO_DERIVADO_DRE. No son asistencia diaria observada ni sustento de pago.\n"
            "Mes fuente y mes destino se conservan. Guion como cero es un supuesto sintético; vacío sigue desconocido.\n"
            "Features promedian por registros laborales con conteo conocido; no personas únicas ni días esperados.\n"
            "Puntuación mayor significa perfil más atípico según el modelo, no probabilidad de fraude.\n"
            "Exactitud y utilidad real: NO_MEDIDO. Las copias perturbadas evalúan sensibilidad sintética.\n"
            "El manifiesto contiene componentes, particiones y resultados de ambos métodos.\n"
            "Instalar el entorno con uv sync --frozen; el modelo incluye imputación, referencia y configuración.\n"
            "Solo cargar modelo.joblib procedente de este servidor y con SHA-256 verificado.\n"
            "Inferencia: asistia.experimental.modelo.inferir(bytes_modelo, filas_del_lote).\n"
        ).encode(),
    }
    libro = openpyxl.Workbook()
    ws = libro.active
    ws.title = "Leer primero"
    for linea in contenido["LEEME.md"].decode().splitlines():
        ws.append([linea])
    ws.column_dimensions["A"].width = 120
    filas = list(filas_exportacion(reportes, exp["resultados"]))
    for periodo in sorted({f["periodo_destino"] for f in filas}):
        ws = libro.create_sheet(periodo)
        ws.append(list(filas[0]))
        for fila in filas:
            if fila["periodo_destino"] == periodo:
                ws.append([seguro(v) for v in fila.values()])
        ws.freeze_panes = "F2"
        ws.auto_filter.ref = ws.dimensions
        for row in ws.iter_rows(min_row=2, max_col=4, min_col=3):
            for c in row:
                c.number_format = "@"
    buffer_xlsx = io.BytesIO()
    libro.save(buffer_xlsx)
    contenido["reportes_mensuales_sinteticos.xlsx"] = buffer_xlsx.getvalue()
    raiz = Path(__file__).resolve().parents[3]
    for nombre in ("pyproject.toml", "uv.lock"):
        if (raiz / nombre).is_file():
            contenido[nombre] = (raiz / nombre).read_bytes()
    for ruta in Path(__file__).parent.glob("*.py"):
        contenido["codigo/" + ruta.name] = ruta.read_bytes()
    contenido["SHA256SUMS.json"] = json.dumps(
        {
            nombre: hashlib.sha256(valor).hexdigest()
            for nombre, valor in contenido.items()
        },
        indent=2,
    ).encode()
    buffer_zip = io.BytesIO()
    with zipfile.ZipFile(buffer_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for nombre, valor in contenido.items():
            # Fecha fija: descargar de nuevo la misma versión produce el mismo ZIP.
            info = zipfile.ZipInfo(nombre, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, valor)
    return buffer_zip.getvalue()
