"""Vista diaria: propuesta sin escritura y paridad con ajuste/consolidado guardados."""

# ruff: noqa: F811
import uuid

from asistia.web.lecturas import huella_reporte

from .test_ajustes_asistencia import ajustar, datos, dia
from .test_coherencia_reporte import cruce  # noqa: F401
from .test_web import app, client, generar, post  # noqa: F401


def comparar(client, conn, rid, numero, valor="", **extra):
    query = datos(conn, rid, numero, valor)
    query.update(extra)
    return client.get(f"/reportes/{rid}/comparacion-dia", query_string=query)


def test_comparacion_guardada_por_dia_y_mes_sin_escribir(client, conn, cruce):
    rid, _ = cruce
    huella = huella_reporte(conn, rid)
    antes = conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"]
    response = comparar(client, conn, rid, 30)
    assert response.status_code == 200, response.text
    d = response.json
    assert d["calendario"]["codigo"] == "G"
    assert d["declarado"]["codigo"] == d["final"]["codigo"] == "I"
    assert d["final"]["falta"] is True
    assert not d["propuesta"] and d["cruce"]["estado"] == "alerta"
    assert len(d["mes"]) == 31 and not any(x["ajustado"] for x in d["mes"])
    assert huella_reporte(conn, rid) == huella
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"]
        == antes
    )
    assert response.headers["Cache-Control"] == "no-store, private"


def test_propuesta_asistio_paridad_con_guardado_y_declaracion_intacta(
    client, conn, cruce
):
    rid, _ = cruce
    anterior = huella_reporte(conn, rid)
    preview = comparar(client, conn, rid, 30, "local:A")
    assert preview.status_code == 200, preview.text
    d = preview.json
    assert d["propuesta"] and d["final"]["codigo"] == "A"
    assert d["declarado"]["codigo"] == d["guardado"]["marca"]["codigo"] == "I"
    assert d["cruce"]["estado"] == "compatible" and d["final"]["falta"] is False
    assert huella_reporte(conn, rid) == anterior
    assert ajustar(client, conn, rid, 30, "local:A").status_code == 303
    actual = comparar(client, conn, rid, 30).json
    assert actual["final"] == d["final"] and actual["declarado"] == d["declarado"]
    assert not actual["propuesta"] and actual["guardado"]["ajustado"]
    assert actual["cruce"]["caso_resuelto"]
    assert next(x for x in actual["mes"] if x["fecha"] == "2026-07-30")["codigo"] == "A"


def test_no_aplica_restitucion_y_consolidado_comparten_resultado(client, conn, cruce):
    rid, _ = cruce
    d = comparar(client, conn, rid, 4, "NO_APLICA").json
    assert d["declarado"]["codigo"] == "A" and d["final"]["codigo"] == "—"
    assert d["final"]["falta"] is False and d["final"]["remunerado"] is None
    assert d["cruce"]["estado"] == "compatible"
    assert ajustar(client, conn, rid, 4, "NO_APLICA").status_code == 303
    assert generar(client, "BORRADOR").status_code == 303
    salida = conn.execute(
        "SELECT manifiesto FROM web_salida ORDER BY creado_en DESC LIMIT 1"
    ).fetchone()["manifiesto"]
    congelado = next(
        x
        for x in salida["filas"][0]["fuente_calculo"]["cruce_remuneracion"]["dias"]
        if x["fecha"] == "2026-07-04"
    )
    assert congelado["es_falta"] == d["final"]["falta"]
    assert congelado["es_remunerado"] == d["final"]["remunerado"]
    assert congelado["resultado"] == d["final"]["resultado"]
    restauracion = comparar(client, conn, rid, 4, "RESTAURAR").json
    assert (
        restauracion["final"]["codigo"] == "A"
        and restauracion["cruce"]["estado"] == "alerta"
    )
    assert comparar(client, conn, rid, 4).json["final"]["codigo"] == "—"


def test_observaciones_y_calendario_pendiente_no_aparentan_concordancia(
    client, conn, cruce
):
    rid, cv = cruce
    d = comparar(client, conn, rid, 4, "local:I").json
    assert d["cruce"]["estado"] == "alerta"
    assert any("Inasistencia declarada" in x for x in d["cruce"]["motivos"])
    assert (
        comparar(client, conn, rid, 2, "NO_APLICA").json["cruce"]["estado"] == "alerta"
    )
    conn.execute(
        "UPDATE calendarizacion_version SET estado='BORRADOR',aprobado_por=NULL,aprobado_en=NULL WHERE calendarizacion_version_id=%s",
        (cv,),
    )
    conn.commit()
    d = comparar(client, conn, rid, 30, "local:A").json
    assert d["cruce"]["estado"] == "pendiente"
    assert any("confirmar" in x for x in d["cruce"]["motivos"])


def test_entradas_ajenas_y_fuentes_obsoletas_rechazadas(client, conn, cruce):
    rid, _ = cruce
    for extra in (
        {"huella": "vieja"},
        {"huella_cruce": "vieja"},
        {"huella_opciones": "vieja"},
    ):
        response = comparar(client, conn, rid, 30, **extra)
        assert response.status_code == 409 and response.json["error"]
    assert comparar(client, conn, rid, 30, dia=str(uuid.uuid4())).status_code == 400
    assert comparar(client, conn, rid, 30, "inexistente").status_code == 400
    assert comparar(client, conn, rid, 30, "RESTAURAR").status_code == 400
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    conn.commit()
    assert comparar(client, conn, rid, 30).status_code == 409


def test_guardar_regresa_al_dia_elegido_y_comparar_exige_sesion(client, conn, cruce):
    rid, _ = cruce
    form = datos(conn, rid, 30, "local:A")
    response = post(
        client, f"/reportes/{rid}/ajustes-asistencia", {**form, "volver_dia": "1"}
    )
    assert response.status_code == 303
    assert (
        f"dia_coherencia={dia(conn, 30)['asistencia_dia_id']}#coherencia"
        in response.location
    )
    html = client.get(response.location).text
    assert "Reportado · digitalizado" in html and "Para consolidar" in html
    assert "data-comparison-days" in html and "reporte_comparacion.js" in html
    with client.session_transaction() as session:
        session.clear()
    assert client.get(f"/reportes/{rid}/comparacion-dia").status_code == 302


def test_selector_y_detalle_no_ocultan_remuneracion_indeterminada(client, conn, cruce):
    rid, _ = cruce
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=jsonb_set(clasificacion_codigos,'{A,es_remunerado}','null') WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    conn.commit()
    d = comparar(client, conn, rid, 2).json
    assert d["final"]["resultado"] == "PENDIENTE"
    assert d["cruce"]["estado"] == "pendiente"
    assert (
        next(x for x in d["mes"] if x["fecha"] == d["fecha"])["estado"] == "pendiente"
    )
    assert any("remuneración" in m for m in d["cruce"]["motivos"])
