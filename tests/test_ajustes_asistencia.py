"""Ajustes operativos: originales intactos, cruce/DRE efectivos, IF documental."""

# ruff: noqa: F811
import uuid
from datetime import date

from psycopg.types.json import Jsonb

from asistia.consolidado.remuneracion import cruce_persona
from asistia.monitoreo.datos import cargar_fuentes, construir
from asistia.web.ajustes_asistencia import opciones
from asistia.web.lecturas import huella_reporte

from .test_coherencia_reporte import contexto, cruce  # noqa: F401
from .test_web import app, client, generar, post  # noqa: F401


def reporte(conn, rid):
    return conn.execute(
        "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s", (rid,)
    ).fetchone()


def dia(conn, numero):
    return conn.execute(
        "SELECT * FROM asistencia_dia WHERE fecha=%s", (date(2026, 7, numero),)
    ).fetchone()


def datos(conn, rid, numero, valor):
    return {
        "dia": str(dia(conn, numero)["asistencia_dia_id"]),
        "valor": valor,
        "huella": huella_reporte(conn, rid),
        "huella_cruce": contexto(conn, rid)["huella"],
        "huella_opciones": opciones(conn, reporte(conn, rid))["huella"],
        "operacion": str(uuid.uuid4()),
    }


def ajustar(client, conn, rid, numero, valor):
    return post(
        client, f"/reportes/{rid}/ajustes-asistencia", datos(conn, rid, numero, valor)
    )


def caso(conn, rid, numero):
    return next(
        c for c in contexto(conn, rid)["casos"] if c["fecha"] == f"2026-07-{numero:02}"
    )


def test_falta_a_presencia_conserva_ocr_validado_y_senales_if(client, conn, cruce):
    rid, _ = cruce
    antes = dia(conn, 30)
    if_antes = construir(
        cargar_fuentes(conn, 2026), reglas_aplicadas=True, exigir_vigente=True
    )
    r = ajustar(client, conn, rid, 30, "local:A")
    assert r.status_code == 303 and r.location.endswith("#coherencia"), r.text
    despues = dia(conn, 30)
    assert {k: v for k, v in antes.items() if k != "evidencia_interpretacion"} == {
        k: v for k, v in despues.items() if k != "evidencia_interpretacion"
    }
    assert reporte(conn, rid)["estado"] == "VALIDADO"
    c = caso(conn, rid, 30)
    assert c["resuelto"] and not c["alertas"] and c["declaracion"] == "PRESENCIA"
    assert c["declarado"]["declaracion"] == "INASISTENCIA"
    assert (
        construir(
            cargar_fuentes(conn, 2026), reglas_aplicadas=True, exigir_vigente=True
        )
        == if_antes
    )
    html = client.get(f"/reportes/{rid}").text
    assert "Asistencia ajustada por RRHH" in html and "Ajuste RRHH:" in html
    assert (
        "Ajustado · sin diferencias" in html
        and 'data-digitization-reviewed="true"' in html
    )
    assert contexto(conn, rid)["pendientes"] == 3


def test_no_correspondia_sin_falta_y_reversible(client, conn, cruce):
    rid, _ = cruce
    assert ajustar(client, conn, rid, 4, "NO_APLICA").status_code == 303
    c = caso(conn, rid, 4)
    assert c["resuelto"] and c["declaracion"] == "NO_APLICA" and not c["alertas"]
    d = dia(conn, 4)
    remuneracion = cruce_persona(conn, d["trabajador_en_reporte_id"])
    resultado = next(x for x in remuneracion["dias"] if x["fecha"] == "2026-07-04")
    assert resultado["es_falta"] is False and resultado["resultado"] == "NO_APLICA"
    assert resultado["es_remunerado"] is None
    assert d["codigo_reportado_raw"] == "A"
    assert sum(remuneracion["resumen"].values()) == remuneracion["total_dias"]
    assert (
        "Sin obligación de asistir"
        in client.get(f"/reportes/{rid}/personas/{d['trabajador_en_reporte_id']}").text
    )
    salida = generar(client, "BORRADOR")
    assert salida.status_code == 303
    assert "1 sin obligación de asistir" in client.get(salida.location).text
    assert ajustar(client, conn, rid, 4, "RESTAURAR").status_code == 303
    assert (
        caso(conn, rid, 4)["declaracion"] == "PRESENCIA"
        and not caso(conn, rid, 4)["resuelto"]
    )
    assert "ajuste_rrhh" not in dia(conn, 4)["evidencia_interpretacion"]
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_revision_evento WHERE accion='AJUSTAR_ASISTENCIA_RRHH'"
        ).fetchone()["n"]
        == 2
    )


def test_ajuste_no_fabrica_coherencia_y_admite_dia_sin_alerta(client, conn, cruce):
    rid, _ = cruce
    assert ajustar(client, conn, rid, 4, "local:I").status_code == 303
    c = caso(conn, rid, 4)
    assert not c["resuelto"] and "INASISTENCIA_SIN_ACTIVIDAD" in c["alertas"]
    assert ajustar(client, conn, rid, 2, "NO_APLICA").status_code == 303
    assert "NO_APLICA_CON_ACTIVIDAD" in caso(conn, rid, 2)["alertas"]
    assert ajustar(client, conn, rid, 2, "local:I").status_code == 303
    assert (
        caso(conn, rid, 2)["resuelto"]
        and caso(conn, rid, 2)["declaracion"] == "INASISTENCIA"
    )


def test_reintento_conflicto_ajeno_y_opcion_obsoleta(client, conn, cruce):
    rid, _ = cruce
    data = datos(conn, rid, 30, "local:A")
    url = f"/reportes/{rid}/ajustes-asistencia"
    assert post(client, url, data).status_code == 303
    assert post(client, url, data).status_code == 303
    assert post(client, url, {**data, "valor": "NO_APLICA"}).status_code == 409
    assert (
        post(client, url, {**data, "operacion": str(uuid.uuid4())}).status_code == 409
    )
    fresh = datos(conn, rid, 4, "NO_APLICA")
    assert post(client, url, {**fresh, "dia": str(uuid.uuid4())}).status_code == 400
    assert post(client, url, {**fresh, "valor": "local:INEXISTENTE"}).status_code == 400
    assert (
        post(client, url, {**fresh, "huella_opciones": "obsoleta"}).status_code == 409
    )
    assert post(client, url, {**fresh, "huella_cruce": "obsoleta"}).status_code == 409
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_revision_evento WHERE accion='AJUSTAR_ASISTENCIA_RRHH'"
        ).fetchone()["n"]
        == 1
    )


def test_cambio_de_declaracion_invalida_ajuste(client, conn, cruce):
    rid, _ = cruce
    assert ajustar(client, conn, rid, 30, "local:A").status_code == 303
    clasificacion = reporte(conn, rid)["clasificacion_codigos"]
    clasificacion["I"]["tipo_dia"] = "Inasistencia con declaración rectificada"
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s WHERE reporte_asistencia_id=%s",
        (Jsonb(clasificacion), rid),
    )
    conn.commit()
    c = caso(conn, rid, 30)
    assert not c["resuelto"] and not c["ajustes"][0]["vigente"]
    assert c["pendiente"] == "AJUSTE_RRHH_OBSOLETO"
    assert generar(client, "REVISADO").status_code == 409
    assert ajustar(client, conn, rid, 30, "local:A").status_code == 303
    assert caso(conn, rid, 30)["resuelto"]


def test_cambio_de_calendario_reabre_cruce_sin_borrar_ajuste(client, conn, cruce):
    rid, cv = cruce
    assert ajustar(client, conn, rid, 30, "local:A").status_code == 303
    assert caso(conn, rid, 30)["resuelto"]
    conn.execute(
        "UPDATE dia_calendarizacion SET codigo_reportado_raw='D',tipo_dia_id=(SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno='NO_LECTIVO_NI_GESTION') WHERE calendarizacion_version_id=%s AND fecha='2026-07-30'",
        (cv,),
    )
    conn.commit()
    c = caso(conn, rid, 30)
    assert c["ajustes"][0]["vigente"] and not c["resuelto"]
    assert "PRESENCIA_SIN_ACTIVIDAD" in c["alertas"]
    assert c["declarado"]["declaracion"] == "INASISTENCIA"
    assert generar(client, "REVISADO").status_code == 409
    assert ajustar(client, conn, rid, 30, "NO_APLICA").status_code == 303
    assert caso(conn, rid, 30)["resuelto"]


def test_consolidado_usa_ajuste_congela_fuente_y_original_exportado(
    client, conn, cruce
):
    rid, _ = cruce
    anterior = generar(client, "BORRADOR")
    assert anterior.status_code == 303
    archivo = client.get(anterior.location + "/descargar").data
    assert ajustar(client, conn, rid, 30, "local:A").status_code == 303
    nuevo = generar(client, "BORRADOR")
    assert nuevo.status_code == 303, nuevo.text
    salida = conn.execute(
        "SELECT manifiesto FROM web_salida ORDER BY creado_en DESC LIMIT 1"
    ).fetchone()["manifiesto"]
    cid = conn.execute(
        "SELECT consolidado_dre_id FROM consolidado_dre ORDER BY version DESC LIMIT 1"
    ).fetchone()["consolidado_dre_id"]
    cuentas = conn.execute(
        "SELECT c.codigo,e.cantidad_dias FROM consolidado_dre_detalle_estado e JOIN consolidado_dre_detalle d USING(consolidado_dre_detalle_id) JOIN catalogo_estado_asistencia c USING(estado_asistencia_id) WHERE d.consolidado_dre_id=%s",
        (cid,),
    ).fetchall()
    assert {c["codigo"]: c["cantidad_dias"] for c in cuentas} == {"A": 29, "F": 1}
    fila = salida["filas"][0]["fuente_calculo"]
    assert "2026-07-30" not in fila["clasificacion_faltas"]["injustificadas"]
    assert "2026-07-30" in fila["clasificacion_reportada"]["injustificadas"]
    assert any(c["ajustes"] for c in salida["coherencia"][str(rid)]["casos"])
    assert client.get(anterior.location + "/descargar").data == archivo
    assert "Hay fuentes o decisiones posteriores" in client.get(anterior.location).text


def test_pendiente_no_ajusta_y_categoria_global_conserva_version(client, conn, cruce):
    rid, _ = cruce
    from .test_catalogo_general import crear

    crear(conn, nombre="Día laborado")
    conn.commit()
    opts = opciones(conn, reporte(conn, rid))["opciones"]
    global_a = next(
        o
        for o in opts
        if o["id"].startswith("global:")
        and o["categoria"]["estado_asistencia_codigo"] == "A"
    )
    assert ajustar(client, conn, rid, 30, global_a["id"]).status_code == 303
    guardado = dia(conn, 30)["evidencia_interpretacion"]["ajuste_rrhh"]
    assert (
        guardado["categoria"]["categoria_general"]["version"]
        == global_a["categoria"]["categoria_general"]["version"]
    )
    assert guardado["categoria"]["es_falta"] is False
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    conn.commit()
    assert ajustar(client, conn, rid, 4, "NO_APLICA").status_code == 409
