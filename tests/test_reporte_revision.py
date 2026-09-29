"""Comparación de reportes: datos reales del esquema, fuentes y personas ficticias."""

# ruff: noqa: F811
import re
import uuid
from datetime import date

from psycopg.types.json import Jsonb

from asistia.leyendas import normalizar_leyenda
from asistia.web.lecturas import huella_reporte
from asistia.web.reporte_revision import posicion_celda, preparar

from .test_web import app, ciclo, client, post  # noqa: F401


def dia(conn):
    return conn.execute(
        "SELECT * FROM asistencia_dia ORDER BY fecha LIMIT 1"
    ).fetchone()


def test_comparacion_mes_completo_sin_aprobar_en_get(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    antes = huella_reporte(conn, rid)
    html = client.get(f"/reportes/{rid}").text
    assert "report-grid-scroll" in html and "31 fechas" not in html
    assert "data-digitization-status>Estado: Pendiente de revisión</span>" in html
    assert (
        html.count("data-report-day ")
        == conn.execute("SELECT count(*) AS n FROM asistencia_dia").fetchone()["n"]
    )
    assert "Documento original" in html and "Datos digitalizados" in html
    assert 'data-report-case="codigo-' in html
    assert "Ver evidencia de la leyenda" not in html and "<pre>" not in html
    assert huella_reporte(conn, rid) == antes
    assert (
        conn.execute("SELECT estado FROM reporte_asistencia").fetchone()["estado"]
        != "VALIDADO"
    )


def test_leyenda_pendiente_se_agrupa_y_marca_aceptada_no_pisa_original(
    client, conn, tmp_path
):
    rid, _ = ciclo(client, conn, tmp_path)
    d = dia(conn)
    response = post(
        client,
        f"/reportes/{rid}/dias/{d['asistencia_dia_id']}",
        {
            "huella": huella_reporte(conn, rid),
            "codigo": "I",
            "captura": "REGISTRADO",
            "motivo": "La fuente ficticia declara I; se corrige A.",
            "volver": "reporte",
        },
    )
    assert response.status_code == 303 and response.location == f"/reportes/{rid}"
    html = client.get(response.location).text
    assert 'data-code="I"' in html and 'data-received="A"' in html
    assert "Marca cotejada o corregida" in html and "A → I" in html
    # El código A se interpreta una vez, aunque aparezca en varias fechas.
    assert html.count('data-target-code="A"') == 1
    assert (
        conn.execute(
            "SELECT codigo_reportado_raw FROM asistencia_dia WHERE asistencia_dia_id=%s",
            (d["asistencia_dia_id"],),
        ).fetchone()["codigo_reportado_raw"]
        == "A"
    )


def test_correccion_desde_comparacion_reenvio_y_conflicto(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    d = dia(conn)
    data = {
        "huella": huella_reporte(conn, rid),
        "codigo": "A",
        "captura": "REGISTRADO",
        "motivo": "Cotejo ficticio",
        "volver": "reporte",
        "operacion": str(uuid.uuid4()),
    }
    url = f"/reportes/{rid}/dias/{d['asistencia_dia_id']}"
    assert post(client, url, data, headers={"X-ASISTIA-Async": "1"}).json == {
        "redirect": f"/reportes/{rid}"
    }
    assert post(client, url, data).status_code == 303
    assert (
        conn.execute("SELECT count(*) AS n FROM web_revision_evento").fetchone()["n"]
        == 1
    )
    data["operacion"] = str(uuid.uuid4())
    data["codigo"] = "I"
    assert post(client, url, data).status_code == 409
    assert (
        conn.execute(
            "SELECT codigo_interpretado FROM asistencia_dia WHERE asistencia_dia_id=%s",
            (d["asistencia_dia_id"],),
        ).fetchone()["codigo_interpretado"]
        == "A"
    )


def test_fuente_resalta_celda_y_expone_todo_el_mes(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    html = client.get(
        f"/reportes/{rid}/fuente?vista=celdas&celda=AL12&fila=7&columna=33"
    ).text
    assert 'aria-label="Celda seleccionada AL12"' in html
    assert "60 columnas" in html and "data-source-zoom" in html
    assert (
        client.get(
            f"/reportes/{rid}/fuente?vista=celdas&celda=%3Cscript%3E"
        ).status_code
        == 400
    )
    assert (
        client.get(f"/reportes/{rid}/fuente?vista=celdas&fila=mal").status_code == 400
    )


def test_posicion_celda_acotada():
    assert posicion_celda("$AL$12:$AM$12") == {
        "celda": "AL12",
        "fila": 12,
        "columna": 38,
    }
    for value in ["A0", "A999999", "ZZZ12", "A1;script", "../A1", "Hoja!A1", None]:
        assert posicion_celda(value) is None


def test_sin_observaciones_no_inventa_confianza_y_get_no_muta(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    cats = normalizar_leyenda(
        [
            {"codigo": c, "descripcion": desc}
            for c, desc in [
                ("A", "Asistencia"),
                ("I", "Inasistencia injustificada"),
                ("J", "Inasistencia justificada"),
            ]
        ],
        "asistencia",
    )
    for c in cats.values():
        c["es_remunerado"] = True
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s", (Jsonb(cats),)
    )
    conn.execute(
        "UPDATE asistencia_dia SET estado_captura='NO_APLICA',validado=true WHERE estado_captura IN ('VACIO','PENDIENTE','ILEGIBLE')"
    )
    conn.execute("UPDATE validacion_reporte SET estado='RESUELTA'")
    conn.commit()
    before = huella_reporte(conn, rid)
    html = client.get(f"/reportes/{rid}").text
    assert "Sin pendientes de digitalización" in html
    assert "No se estima un porcentaje de certeza" in html
    assert huella_reporte(conn, rid) == before
    assert (
        conn.execute("SELECT estado FROM reporte_asistencia").fetchone()["estado"]
        != "VALIDADO"
    )


def test_leyenda_guardada_actualiza_pendiente_e_historial(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    response = post(
        client,
        f"/reportes/{rid}/leyenda",
        {
            "huella": huella_reporte(conn, rid),
            "codigo": "A",
            "tipo_dia": "Asistencia",
            "remuneracion": "SI",
            "es_falta": "NO",
            "motivo": "Regla ficticia cotejada con la fuente",
        },
    )
    assert response.status_code == 303
    html = client.get(response.location).text
    assert (
        "Leyenda revisada" in html and "Regla ficticia cotejada con la fuente" in html
    )
    assert "<pre>" not in html
    assert "Remuneración" in html and "Pendiente → Remunerado" in html
    pendientes = re.search(
        r'<select id="report-case"[^>]*>(.*?)</select>', html, re.DOTALL
    )
    assert 'value="codigo-0"' not in pendientes[1]
    # La lista independiente de símbolos conserva el acceso a A ya resuelta.
    simbolos = re.search(r'<select id="report-symbol">(.*?)</select>', html, re.DOTALL)
    assert 'value="codigo-0"' in simbolos[1]


def test_version_anterior_se_consulta_sin_formularios_de_revision(
    client, conn, tmp_path
):
    rid, _ = ciclo(client, conn, tmp_path)
    # Crear una sucesora conservando serie/documento; no hace falta reextraer para este contrato.
    conn.execute(
        "INSERT INTO reporte_asistencia(documento_recibido_id,reporte_asistencia_serie_id,institucion_educativa_id,periodo,hoja_pagina_origen,indice_bloque,version,estado,tipo_fuente,institucion_reportada_raw) SELECT documento_recibido_id,reporte_asistencia_serie_id,institucion_educativa_id,periodo,hoja_pagina_origen,indice_bloque,version+1,'IMPORTADO',tipo_fuente,institucion_reportada_raw FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    conn.commit()
    html = client.get(f"/reportes/{rid}").text
    assert "Versión anterior, disponible para consulta" in html
    assert "data-day-form" not in html and "data-report-form" not in html
    assert "Registrar reporte revisado" not in html


def test_fecha_fuera_del_mes_no_desaparece_y_no_se_duplica(app):
    r = {
        "periodo": date(2026, 2, 1),
        "reporte_asistencia_id": uuid.uuid4(),
        "nombre_original": "ficticio.pdf",
        "hoja_pagina_origen": "2",
        "clasificacion_codigos": {},
    }
    pid = uuid.uuid4()
    p = {
        "trabajador_en_reporte_id": pid,
        "trabajador_id": 1,
        "rol_laboral_id": 1,
        "nombres_reportados_raw": "Persona ficticia",
    }
    d = {
        "asistencia_dia_id": uuid.uuid4(),
        "trabajador_en_reporte_id": pid,
        "fecha": date(2026, 3, 1),
        "estado_captura": "VACIO",
        "codigo_reportado_raw": None,
        "codigo_interpretado": None,
        "celda_origen": "pagina3:fila1",
        "validado": False,
        "evidencia_interpretacion": {},
    }
    with app.test_request_context():
        vista = preparar(r, [p], [d], [], [], [])
    assert len(vista["fechas"]) == 29 and p["celdas"][-1] is d
    assert sum(c is not None for c in p["celdas"]) == 1
    assert d["fuente_url"].endswith("#page=3")


def test_confirmar_vacio_conserva_estado_sin_inferir_asistencia(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    primero = dia(conn)
    conn.execute(
        "UPDATE asistencia_dia SET estado_captura='VACIO',estado_asistencia_id=NULL,codigo_reportado_raw=NULL,codigo_interpretado=NULL WHERE asistencia_dia_id=%s",
        (primero["asistencia_dia_id"],),
    )
    conn.commit()
    d = conn.execute(
        "SELECT * FROM asistencia_dia WHERE asistencia_dia_id=%s",
        (primero["asistencia_dia_id"],),
    ).fetchone()
    assert d
    response = post(
        client,
        f"/reportes/{rid}/dias/{d['asistencia_dia_id']}",
        {
            "huella": huella_reporte(conn, rid),
            "captura": "VACIO",
            "motivo": "Se cotejó que el original está vacío.",
            "volver": "reporte",
        },
    )
    assert response.status_code == 303
    saved = conn.execute(
        "SELECT * FROM asistencia_dia WHERE asistencia_dia_id=%s",
        (d["asistencia_dia_id"],),
    ).fetchone()
    assert saved["estado_captura"] == "VACIO" and saved["estado_asistencia_id"] is None
    assert saved["codigo_reportado_raw"] == d["codigo_reportado_raw"]


def test_previsualizacion_imagen_con_zoom_conserva_original(
    client, app, tmp_path, monkeypatch
):
    import base64

    from asistia.web import routes

    rid = uuid.uuid4()
    path = tmp_path / "original.png"
    original = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/nxoAAAAASUVORK5CYII="
    )
    path.write_bytes(original)
    monkeypatch.setattr(
        routes,
        "ruta_original_reporte",
        lambda _: ({"reporte_asistencia_id": rid, "nombre_original": "foto.png"}, path),
    )
    html = client.get(f"/reportes/{rid}/fuente")
    assert (
        html.status_code == 200
        and "data-viewer-zoom" in html.text
        and "data-viewer-image" in html.text
    )
    response = client.get(f"/reportes/{rid}/fuente?archivo=1")
    assert response.data == original and response.mimetype == "image/png"
    assert app.test_client().get(f"/reportes/{rid}/fuente?archivo=1").status_code == 302


def test_word_previsualiza_pdf_y_conserva_descarga_si_conversion_falla(
    client, tmp_path, monkeypatch
):
    from asistia.cierre import conversion
    from asistia.web import routes

    rid = uuid.uuid4()
    original = tmp_path / "fuente.docx"
    original.write_bytes(b"Word ficticio, conversion sustituida en prueba")
    pdf = tmp_path / "representacion.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%Representacion ficticia")
    monkeypatch.setattr(
        routes,
        "ruta_original_reporte",
        lambda _: (
            {"reporte_asistencia_id": rid, "nombre_original": "fuente.docx"},
            original,
        ),
    )
    monkeypatch.setattr(conversion, "representar_docx", lambda path: (pdf, {}))
    response = client.get(f"/reportes/{rid}/fuente?vista=celdas")
    assert response.mimetype == "application/pdf" and response.data == pdf.read_bytes()

    def falla(path):
        raise ValueError("Conversor no disponible")

    monkeypatch.setattr(conversion, "representar_docx", falla)
    response = client.get(f"/reportes/{rid}/fuente?vista=celdas")
    assert response.status_code == 200 and "Descargar original" in response.text
    assert original.read_bytes() == b"Word ficticio, conversion sustituida en prueba"
