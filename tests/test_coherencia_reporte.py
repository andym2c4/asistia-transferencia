"""RF-F17: digitalización fiel y cruces pendientes son estados independientes."""

# ruff: noqa: F811
import io
import uuid
from datetime import date

import openpyxl
import pytest
from psycopg.types.json import Jsonb

from asistia.coherencia import revisar_reportes
from asistia.monitoreo.datos import cargar_fuentes, construir
from asistia.monitoreo.reglas import evaluar
from asistia.web.lecturas import huella_reporte
from asistia.web.leyendas import huella_calendario

from .test_web import app, ciclo, client, generar, post, revisar  # noqa: F401


@pytest.fixture
def cruce(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    uid = conn.execute("SELECT usuario_id FROM usuario LIMIT 1").fetchone()[
        "usuario_id"
    ]
    cv = conn.execute(
        "SELECT calendarizacion_version_id FROM calendarizacion_version"
    ).fetchone()["calendarizacion_version_id"]
    cc = {
        "L": {
            "tipo_dia": "Lectivo",
            "grupo_actividad": "LECTIVO",
            "es_remunerado": True,
        },
        "G": {
            "tipo_dia": "Gestión",
            "grupo_actividad": "GESTION",
            "es_remunerado": True,
        },
        "D": {
            "tipo_dia": "Descanso",
            "grupo_actividad": "NO_LECTIVO_NI_GESTION",
            "es_remunerado": False,
        },
    }
    ca = {
        "A": {
            "tipo_dia": "Día laborado",
            "estado_asistencia_codigo": "A",
            "es_remunerado": True,
            "es_falta": False,
        },
        "F": {
            "tipo_dia": "Feriado",
            "estado_asistencia_codigo": "F",
            "es_remunerado": False,
            "es_falta": False,
        },
        "I": {
            "tipo_dia": "Inasistencia injustificada",
            "estado_asistencia_codigo": "I",
            "es_remunerado": False,
            "es_falta": True,
        },
    }
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos=%s,estado='VIGENTE',aprobado_por=%s,aprobado_en=now() WHERE calendarizacion_version_id=%s",
        (Jsonb(cc), uid, cv),
    )
    conn.execute(
        "UPDATE dia_calendarizacion SET codigo_reportado_raw=CASE WHEN extract(day FROM fecha)=4 THEN 'D' WHEN extract(day FROM fecha)>=29 THEN 'G' ELSE 'L' END WHERE calendarizacion_version_id=%s",
        (cv,),
    )
    conn.execute(
        "UPDATE dia_calendarizacion d SET tipo_dia_id=c.tipo_dia_id FROM catalogo_tipo_dia c WHERE d.calendarizacion_version_id=%s AND c.codigo_interno=CASE WHEN d.codigo_reportado_raw='D' THEN 'NO_LECTIVO_NI_GESTION' WHEN d.codigo_reportado_raw='G' THEN 'GESTION' ELSE 'LECTIVO' END",
        (cv,),
    )
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s WHERE reporte_asistencia_id=%s",
        (Jsonb(ca), rid),
    )
    conn.execute(
        """INSERT INTO asistencia_dia(trabajador_en_reporte_id,fecha,codigo_reportado_raw,estado_captura,estado_asistencia_id)
      SELECT t.trabajador_en_reporte_id,d::date,'A','REGISTRADO',(SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo='A')
      FROM trabajador_en_reporte t CROSS JOIN generate_series('2026-07-01'::date,'2026-07-31'::date,'1 day') d
      WHERE t.reporte_asistencia_id=%s ON CONFLICT (trabajador_en_reporte_id,fecha) DO UPDATE SET codigo_reportado_raw='A',estado_captura='REGISTRADO',estado_asistencia_id=excluded.estado_asistencia_id,evidencia_interpretacion='{}',codigo_interpretado=NULL""",
        (rid,),
    )
    for day, code in [(29, "F"), (30, "I")]:
        conn.execute(
            "UPDATE asistencia_dia SET codigo_reportado_raw=%s,estado_asistencia_id=(SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo=%s) WHERE fecha=%s",
            (code, code, date(2026, 7, day)),
        )
    conn.execute(
        "UPDATE asistencia_dia SET codigo_reportado_raw=NULL,estado_captura='NO_APLICA',estado_asistencia_id=NULL,evidencia_interpretacion=%s WHERE fecha='2026-07-31'",
        (Jsonb({"no_correspondia_asistir": True}),),
    )
    conn.commit()
    assert revisar(client, conn, rid).status_code == 303
    return rid, cv


def contexto(conn, rid):
    r = conn.execute(
        "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s", (rid,)
    ).fetchone()
    return revisar_reportes(conn, [r])[str(rid)]


def decidir(client, conn, rid, **extra):
    c = contexto(conn, rid)
    datos = {
        "casos": [x["id"] for x in c["casos"]],
        "decision": "VERIFICADO",
        "huella": huella_reporte(conn, rid),
        "huella_cruce": c["huella"],
        **extra,
    }
    return post(client, f"/reportes/{rid}/coherencia", datos)


def test_get_cruces_agrupados_fuentes_intactas_y_paridad_con_modelo(
    client, conn, cruce
):
    rid, cv = cruce
    before = (huella_reporte(conn, rid), huella_calendario(conn, cv))
    c = contexto(conn, rid)
    assert c["pendientes"] == 4 and c["evaluables"] == 31 and not c["listo"]
    assert len(c["casos"][0]["alertas"]) == 2  # Una fecha, dos señales.
    assert c["alertas_por_regla"]["INASISTENCIA_EN_GESTION"] == 1
    assert c["alertas_por_regla"]["NO_APLICA_CON_ACTIVIDAD"] == 1
    html = client.get(f"/reportes/{rid}").text
    assert "Digitalización: <strong>revisada" in html and "4 casos por revisar" in html
    assert "No laborable declarado" in html and "data-coherence-form" in html
    p = construir(
        cargar_fuentes(conn, 2026), reglas_aplicadas=True, exigir_vigente=True
    )[0]["datos"]
    assert p["dias_con_alerta"] == len(c["casos"]) and p["alertas_por_regla"] == {
        k: c["alertas_por_regla"].get(k, 0) for k in p["alertas_por_regla"]
    }
    assert p["componentes"]["tasa_inasistencia_en_gestion"]["numerador"] == 1
    assert p["componentes"]["tasa_inasistencia_en_gestion"]["denominador"] == 3
    assert before == (huella_reporte(conn, rid), huella_calendario(conn, cv))
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_revision_evento WHERE accion='REVISAR_COHERENCIA'"
        ).fetchone()["n"]
        == 0
    )


def test_decision_sin_comentario_idempotente_no_reescribe_ocr(client, conn, cruce):
    rid, cv = cruce
    before = (huella_reporte(conn, rid), huella_calendario(conn, cv))
    c = contexto(conn, rid)
    op = str(uuid.uuid4())
    data = {"operacion": op, "huella_cruce": c["huella"]}
    assert decidir(client, conn, rid, **data).status_code == 303
    assert decidir(client, conn, rid, **data).status_code == 303
    assert decidir(client, conn, rid, **data, decision="PENDIENTE").status_code == 409
    assert contexto(conn, rid)["listo"]
    assert before == (huella_reporte(conn, rid), huella_calendario(conn, cv))
    event = conn.execute(
        "SELECT * FROM web_revision_evento WHERE accion='REVISAR_COHERENCIA'"
    ).fetchone()
    assert event["usuario_id"] and len(event["anterior"]["casos"]) == 4
    assert "sin comentario adicional" in event["motivo"]
    assert decidir(client, conn, rid, decision="PENDIENTE").status_code == 303
    assert contexto(conn, rid)["pendientes"] == 4


def test_cambio_calendario_reabre_casos_y_rechaza_formulario_obsoleto(
    client, conn, cruce
):
    rid, cv = cruce
    old = contexto(conn, rid)
    assert decidir(client, conn, rid).status_code == 303
    assert decidir(client, conn, rid, huella_cruce=old["huella"]).status_code == 409
    # Cambio relevante de la evidencia; misma contradicción requiere otro cotejo.
    conn.execute(
        "UPDATE dia_calendarizacion SET celda_origen='Z99' WHERE calendarizacion_version_id=%s AND fecha='2026-07-04'",
        (cv,),
    )
    conn.commit()
    assert contexto(conn, rid)["pendientes"] == 1
    assert decidir(client, conn, rid, casos=["0" * 64]).status_code == 409


def test_consolidado_bloquea_revisado_permite_borrador_y_congela_cruce(
    client, conn, cruce
):
    rid, _ = cruce
    assert generar(client, "REVISADO").status_code == 409
    r = generar(client, "BORRADOR")
    assert r.status_code == 303, r.text
    content = client.get(r.location + "/descargar").data
    w = openpyxl.load_workbook(io.BytesIO(content))
    assert len(w.sheetnames) == 1 and "4 caso(s)" in w.active["A7"].value
    conservado = conn.execute(
        "SELECT manifiesto FROM web_salida WHERE estado_revision='BORRADOR'"
    ).fetchone()["manifiesto"]
    assert conservado["coherencia"][str(rid)]["pendientes"] == 4
    assert decidir(client, conn, rid).status_code == 303
    reviewed = generar(client, "REVISADO")
    assert reviewed.status_code == 303, reviewed.text
    m = conn.execute(
        "SELECT manifiesto FROM web_salida WHERE estado_revision='REVISADO'"
    ).fetchone()["manifiesto"]
    assert (
        m["coherencia"][str(rid)]["listo"]
        and m["coherencia"][str(rid)]["resueltos"] == 4
    )
    assert client.get(r.location + "/descargar").data == content
    assert "Hay fuentes o decisiones posteriores" in client.get(r.location).text
    assert decidir(client, conn, rid, decision="PENDIENTE").status_code == 303
    assert generar(client, "REVISADO").status_code == 409


def test_fuentes_insuficientes_no_se_declaran_coherentes(client, conn, cruce):
    rid, cv = cruce
    conn.execute(
        "UPDATE calendarizacion_version SET estado='BORRADOR',aprobado_por=NULL,aprobado_en=NULL WHERE calendarizacion_version_id=%s",
        (cv,),
    )
    conn.commit()
    c = contexto(conn, rid)
    assert not c["casos"] and not c["listo"] and c["no_evaluables"] == 31
    assert "CALENDARIO_SIN_CONFIRMAR" in {i["codigo"] for i in c["impedimentos"]}
    assert generar(client, "REVISADO").status_code == 409
    assert decidir(client, conn, rid).status_code == 400


def test_no_inferir_falta_por_f_ni_por_gestion_o_vacio():
    cal = {"grupo_actividad": "GESTION", "es_remunerado": True}
    assert evaluar(
        cal, {"tipo_dia": "Feriado", "estado_asistencia_codigo": "F", "es_falta": False}
    )["alertas"] == ["NO_LABORABLE_CON_ACTIVIDAD"]
    assert evaluar(cal, {})["alertas"] == []
    assert evaluar(cal, {"estado_asistencia_codigo": "I"})["alertas"] == [
        "INASISTENCIA_EN_GESTION"
    ]
    assert evaluar(cal, {}, no_aplica=True)["alertas"] == ["NO_APLICA_CON_ACTIVIDAD"]
    assert evaluar(cal, {"estado_asistencia_codigo": "L"})["alertas"] == []


def test_csrf_historica_y_seleccion_ajena(client, app, conn, cruce):
    rid, _ = cruce
    assert app.test_client().get(f"/reportes/{rid}").status_code == 302
    assert client.post(f"/reportes/{rid}/coherencia").status_code == 400
    assert decidir(client, conn, rid, decision="APROBAR_PAGO").status_code == 400
    conn.execute(
        "UPDATE reporte_asistencia SET estado='HISTORICA' WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    conn.commit()
    assert not contexto(conn, rid)["listo"]
    assert decidir(client, conn, rid, casos=["0" * 64]).status_code == 409


def test_corte_conserva_senales_y_decisiones_no_son_etiquetas(client, conn, cruce):
    from asistia.monitoreo.cortes import crear, perfiles_corte

    rid, _ = cruce
    cid, nuevo = crear(conn, 2026, autor="PRUEBA", motivo="Corte ficticio RF-F17")
    conn.commit()
    assert nuevo
    c = conn.execute(
        "SELECT manifiesto FROM monitoreo_diario_corte WHERE corte_id=%s", (cid,)
    ).fetchone()["manifiesto"]
    assert c["configuracion_corte"]["version"] == "IF_CRUCES_DIARIOS_2"
    assert len(c["variables"]) == 11 and len(c["reglas"]) == 6
    antes = perfiles_corte(conn, cid)
    assert antes[0]["datos"]["dias_con_alerta"] == 4
    assert decidir(client, conn, rid).status_code == 303
    assert contexto(conn, rid)["listo"]
    assert crear(conn, 2026, autor="PRUEBA", motivo="Repetición ficticia") == (
        cid,
        False,
    )
    assert perfiles_corte(conn, cid) == antes


def test_cruce_usa_categoria_aplicada_sin_sustituir_por_regla_global_nueva():
    from .test_monitoreo_diario import fuente_minima

    fuente = fuente_minima()
    fuente["calendarios"][0]["estado"] = "VIGENTE"
    fuente["catalogo"] = [
        {
            "categoria_id": 1,
            "dominio": "asistencia",
            "nombre": "Asistencia",
            "es_remunerado": False,
            "es_falta": True,
            "categoria_version_id": str(uuid.uuid4()),
            "version": 99,
        }
    ]
    fuente["equivalencias"] = [
        {"dominio": "asistencia", "significado": "ASISTENCIA", "categoria_id": 1}
    ]
    perfil = construir(fuente, reglas_aplicadas=True, exigir_vigente=True)[0]
    assert perfil["datos"]["dias"][0]["asistencia"]["es_remunerado"] is True
    assert perfil["datos"]["dias"][0]["asistencia"]["es_falta"] is False


def test_corte_historico_muestra_solo_reglas_y_variables_de_su_contrato(
    client, conn, cruce
):
    from asistia.monitoreo.cortes import crear, perfiles_corte

    cid, _ = crear(
        conn, 2026, autor="PRUEBA", motivo="Simular contrato histórico ficticio"
    )
    # Simulación exclusivamente en fixture: cuatro reglas y nueve variables antiguas.
    conn.execute(
        """UPDATE monitoreo_diario_corte SET manifiesto=jsonb_set(
        jsonb_set(manifiesto,'{reglas}',(manifiesto->'reglas')-'INASISTENCIA_EN_GESTION'-'NO_APLICA_CON_ACTIVIDAD'),
        '{variables}',(manifiesto->'variables')-'tasa_inasistencia_en_gestion'-'tasa_no_aplica_con_actividad') WHERE corte_id=%s""",
        (cid,),
    )
    conn.execute(
        """UPDATE monitoreo_diario_perfil SET datos=jsonb_set(
        jsonb_set(datos,'{alertas_por_regla}',(datos->'alertas_por_regla')-'INASISTENCIA_EN_GESTION'-'NO_APLICA_CON_ACTIVIDAD'),
        '{componentes}',(datos->'componentes')-'tasa_inasistencia_en_gestion'-'tasa_no_aplica_con_actividad') WHERE corte_id=%s""",
        (cid,),
    )
    conn.commit()
    html = client.get(f"/monitoreo/diario?corte={cid}")
    assert (
        html.status_code == 200
        and "Inasistencia declarada en día de gestión" not in html.text
    )
    pid = perfiles_corte(conn, cid)[0]["perfil_id"]
    html = client.get(f"/monitoreo/diario/perfiles/{pid}")
    assert (
        html.status_code == 200 and 'value="INASISTENCIA_EN_GESTION"' not in html.text
    )
