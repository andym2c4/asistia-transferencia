"""RF-F32: denominadores, revisión vigente y entrega de prioridades a RRHH."""

import copy
from datetime import date
from uuid import uuid4

from psycopg.types.json import Jsonb

from asistia.web.dashboard import analisis, preparar, prioridades
from asistia.web.lecturas import huella_reporte

from .test_coherencia_reporte import cruce, decidir  # noqa: F401
from .test_instituciones import institucion
from .test_web import app, ciclo, client, generar, post  # noqa: F401

# ruff: noqa: F811
MES = date(2026, 7, 1)


def test_metricas_separan_digitalizacion_coherencia_y_corte_conservado(
    app, client, conn, cruce
):
    rid, _ = cruce
    app.config["DEVELOPMENT_TOOLS"] = False
    inicial = preparar(conn, MES, "PRIMARIA")
    assert (
        inicial["total_reportes"],
        inicial["revisados"],
        inicial["porcentaje_revisados"],
    ) == (1, 1, 100)
    assert inicial["listos"] == 0
    assert (
        len(inicial["prioridades"]) == 1
        and inicial["prioridades"][0]["casos_pendientes"] == 4
    )
    assert sum(m["casos"] for m in inicial["prioridades"][0]["motivos"]) > 4
    salida = generar(client).location
    antes = huella_reporte(conn, rid)
    html = client.get("/?periodo=2026-07&nivel=PRIMARIA&estado=pendientes").text
    assert "100.0 %" in html and 'id="dashboard-prioridades"' in html
    assert "Más herramientas" not in html and "/monitoreo" not in html
    assert huella_reporte(conn, rid) == antes
    assert decidir(client, conn, rid).status_code == 303
    final = preparar(conn, MES, "PRIMARIA")
    assert final["listos"] == 1 and not final["prioridades"]
    assert final["meses"][0]["salida"]["estado_revision"] == "BORRADOR"
    assert (
        str(final["meses"][0]["salida"]["web_salida_id"]) == salida.rsplit("/", 1)[-1]
    )
    manifiesto = conn.execute(
        "SELECT manifiesto FROM web_salida WHERE web_salida_id=%s",
        (salida.rsplit("/", 1)[-1],),
    ).fetchone()["manifiesto"]
    assert manifiesto["coherencia"][str(rid)]["pendientes"] == 4


def test_sin_recepcion_porcentaje_no_aplica_y_nivel_no_mezclado(client, conn, tmp_path):
    _rid, _ = ciclo(client, conn, tmp_path)
    faltante = institucion(conn, "9999002", nivel="Primaria")
    institucion(conn, "9999003", nivel="Secundaria")
    conn.commit()
    julio = preparar(conn, MES, "PRIMARIA")
    assert julio["sin_reporte"] == 1
    assert julio["porcentaje_revisados"] == 0
    assert any(
        i["institucion_educativa_id"] == faltante and not i["recibido"]
        for i in julio["instituciones"]
    )
    agosto = preparar(conn, date(2026, 8, 1), "PRIMARIA")
    assert (
        agosto["porcentaje_revisados"] is None and agosto["porcentaje_listos"] is None
    )
    assert agosto["sin_reporte"] == 2
    assert [
        (m["periodo"].month, m["recibidos"], m["porcentaje"]) for m in agosto["meses"]
    ] == [(8, 0, None), (7, 1, 0)]
    assert preparar(conn, MES, "SECUNDARIA")["total_reportes"] == 0
    assert client.get("/?periodo=2026-08&nivel=PRIMARIA").status_code == 200


def test_ultima_version_y_turnos_no_inflan_avance(conn, client, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    r = conn.execute(
        "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s", (rid,)
    ).fetchone()
    for version, estado in [(2, "VALIDADO"), (3, "RECHAZADO"), (4, "HISTORICA")]:
        conn.execute(
            "INSERT INTO reporte_asistencia(reporte_asistencia_serie_id,institucion_educativa_id,documento_recibido_id,periodo,version,tipo_fuente,estado,institucion_reportada_raw) VALUES(%s,%s,%s,%s,%s,'EXCEL_NATIVO',%s,'Ficticia')",
            (
                r["reporte_asistencia_serie_id"],
                r["institucion_educativa_id"],
                r["documento_recibido_id"],
                MES,
                version,
                estado,
            ),
        )
    sid = conn.execute(
        "INSERT INTO reporte_asistencia_serie(institucion_educativa_id,periodo,nivel_modalidad,turno) VALUES(%s,%s,'PRIMARIA','TARDE') RETURNING reporte_asistencia_serie_id",
        (r["institucion_educativa_id"], MES),
    ).fetchone()["reporte_asistencia_serie_id"]
    conn.execute(
        "INSERT INTO reporte_asistencia(reporte_asistencia_serie_id,institucion_educativa_id,documento_recibido_id,periodo,version,tipo_fuente,institucion_reportada_raw,indice_bloque) VALUES(%s,%s,%s,%s,1,'EXCEL_NATIVO','Ficticia',2)",
        (sid, r["institucion_educativa_id"], r["documento_recibido_id"], MES),
    )
    conn.commit()
    d = preparar(conn, MES, "PRIMARIA")
    assert (d["total_reportes"], d["revisados"], d["porcentaje_revisados"]) == (
        2,
        1,
        50,
    )
    assert d["meses"][0]["recibidos"] == 2 and d["meses"][0]["porcentaje"] == 50
    assert len(d["prioridades"]) == 1 and d["listos"] == 0


def test_deduplicacion_de_casos_y_resueltos_no_reaparecen_como_prioridad():
    r = {
        "reporte_asistencia_id": "1",
        "institucion_educativa_id": 1,
        "nombre_ie": "Ficticia",
        "cod_mod": "9999999",
        "anexo": "0",
        "estado": "VALIDADO",
        "criticos": 0,
        "sin_identidad": 0,
    }
    caso = {
        "id": "igual",
        "resuelto": False,
        "reglas": [{"codigo": "PRESENCIA_SIN_ACTIVIDAD"}],
    }
    c = {"listo": False, "casos": [caso], "impedimentos": []}
    dos = [r, {**r, "reporte_asistencia_id": "2"}]
    assert prioridades(dos, {"1": c, "2": c})[0]["casos_pendientes"] == 1
    resuelto = {**c, "listo": True, "casos": [{**caso, "resuelto": True}]}
    assert prioridades(dos, {"1": resuelto, "2": resuelto}) == []


def corte_ficticio_puntuado(conn):
    """Resultados controlados para probar entrega, no evaluación del modelo."""
    cid = uuid4()
    conn.execute(
        "INSERT INTO monitoreo_diario_corte(corte_id,huella,anio,manifiesto,resultados,autor,motivo) VALUES(%s,%s,2026,%s,'[]','PRUEBA','Corte ficticio de interfaz')",
        (
            cid,
            uuid4().hex * 2,
            Jsonb(
                {
                    "evaluacion": {"estado": "ENTRENADO"},
                    "configuracion_corte": {"version": "ANTERIOR"},
                }
            ),
        ),
    )
    scores = []
    for n, nivel, mes, apto, en_cupo in [
        (1, "PRIMARIA", 7, True, True),
        (2, "PRIMARIA", 7, True, False),
        (3, "PRIMARIA", 7, False, False),
        (4, "SECUNDARIA", 7, True, True),
        (5, "PRIMARIA", 8, True, True),
    ]:
        iid = institucion(conn, f"999800{n}", nivel=nivel.capitalize())
        pid = uuid4()
        identidad = {
            "nombre_ie": f"Ficticia {n}",
            "cod_mod": f"999800{n}",
            "anexo": "0",
            "nivel": nivel,
        }
        datos = {
            "apto_modelo": apto,
            "componentes": {
                "tasa_presencia_sin_actividad": {
                    "numerador": 1,
                    "denominador": 4,
                    "valor": 0.25,
                    "estado": "CALCULABLE",
                }
            },
        }
        conn.execute(
            "INSERT INTO monitoreo_diario_perfil(perfil_id,corte_id,institucion_educativa_id,periodo,identidad,datos,fuentes) VALUES(%s,%s,%s,%s,%s,%s,'{}')",
            (pid, cid, iid, date(2026, mes, 1), Jsonb(identidad), Jsonb(datos)),
        )
        if apto:
            scores.append(
                {
                    "reporte_id": str(pid),
                    "score_if": 0.5,
                    "puesto": n + 5,
                    "en_cupo": en_cupo,
                }
            )
    conn.execute(
        "UPDATE monitoreo_diario_corte SET resultados=%s WHERE corte_id=%s",
        (Jsonb(scores), cid),
    )
    conn.commit()
    return cid


def test_modelo_conserva_cupo_orden_fecha_y_ambito_sin_confundir_probabilidad(
    app, client, conn
):
    cid = corte_ficticio_puntuado(conn)
    a = analisis(conn, MES, "PRIMARIA")
    assert (a["total"], a["puntuados"], a["sin_datos"]) == (3, 2, 1)
    assert len(a["filas"]) == 1 and a["filas"][0]["puesto"] == 6
    assert a["filas"][0]["observados"][0]["numerador"] == 1
    assert a["version_anterior"]
    anterior = copy.deepcopy(a)
    app.config["DEVELOPMENT_TOOLS"] = False
    html = client.get(f"/?periodo=2026-07&nivel=PRIMARIA&corte={cid}").text
    assert "Análisis guardado:" in html and "modelo anteriores" in html
    assert "score_if" not in html and "/monitoreo" not in html
    assert 'id="dashboard-modelo"' in html
    assert analisis(conn, MES, "PRIMARIA") == anterior
    assert (
        client.get(f"/?periodo=2027-07&nivel=PRIMARIA&corte={cid}").status_code == 404
    )
    assert client.get("/?corte=no-id").status_code == 400
    assert analisis(conn, date(2026, 8, 1), "PRIMARIA")["total"] == 1
    assert analisis(conn, MES, "SECUNDARIA")["total"] == 1


def test_dashboard_actualiza_corte_sin_pedir_motivo_y_reintento_no_duplica(
    app, client, conn, cruce
):
    app.config["DEVELOPMENT_TOOLS"] = False
    rid, _ = cruce
    antes = huella_reporte(conn, rid)
    assert (
        client.post(
            "/dashboard/analisis", data={"periodo": "2026-07", "nivel": "PRIMARIA"}
        ).status_code
        == 400
    )
    datos = {"periodo": "2026-07", "nivel": "PRIMARIA"}
    r = post(client, "/dashboard/analisis", datos)
    assert r.status_code == 303 and r.location.endswith("#analisis")
    html = client.get(r.location).text
    assert "Todavía no hay suficientes datos comparables" in html
    assert post(client, "/dashboard/analisis", datos).location == r.location
    assert (
        conn.execute("SELECT count(*) n FROM monitoreo_diario_corte").fetchone()["n"]
        == 1
    )
    assert huella_reporte(conn, rid) == antes
    conn.execute(
        "UPDATE asistencia_dia SET codigo_reportado_raw='I' WHERE fecha='2026-07-01'"
    )
    conn.commit()
    nuevo = post(client, "/dashboard/analisis", datos)
    assert nuevo.location != r.location
    assert (
        conn.execute("SELECT count(*) n FROM monitoreo_diario_corte").fetchone()["n"]
        == 2
    )


def test_todas_las_rutas_tecnicas_bloqueadas_incluidos_alias_y_post(app, client):
    app.config["DEVELOPMENT_TOOLS"] = False
    comprobadas = 0
    for regla in app.url_map.iter_rules():
        if not regla.endpoint.startswith(("pages.monitoreo", "pages.experimental")):
            continue
        _, ruta = regla.build({arg: uuid4() for arg in regla.arguments})
        metodo = "POST" if "POST" in regla.methods else "GET"
        r = post(client, ruta) if metodo == "POST" else client.get(ruta)
        assert r.status_code == 404, (regla.endpoint, ruta)
        comprobadas += 1
    assert comprobadas >= 16
    assert client.get("/revision").status_code == 200
