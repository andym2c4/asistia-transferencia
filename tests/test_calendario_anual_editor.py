"""RF-F16: edición anual compacta y herencia explícita de reglas."""

# ruff: noqa: F811
import uuid

from psycopg.types.json import Jsonb

from asistia import categorias as cat
from asistia.web.calendario_revision import propuesta
from asistia.web.leyendas import huella_calendario
from asistia.web.reporte_reglas import contexto_catalogo

from .test_calendario_revision import calendario  # noqa: F401
from .test_web import app, client, post  # noqa: F401


def test_anual_doce_meses_sin_fuente_repetida_y_sin_escrituras(
    client, conn, calendario
):
    _, cvid, _ = calendario
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos=clasificacion_codigos || jsonb_build_object('CODIGO_LARGO',clasificacion_codigos->'D') WHERE calendarizacion_version_id=%s",
        (cvid,),
    )
    conn.execute(
        "UPDATE dia_calendarizacion SET estado_captura='NO_APLICA',tipo_dia_id=NULL,codigo_reportado_raw=NULL WHERE calendarizacion_version_id=%s AND fecha='2026-07-02'",
        (cvid,),
    )
    conn.commit()
    h = huella_calendario(conn, cvid)
    page = client.get(f"/calendarios/{cvid}?mes=9#leyenda")
    assert page.status_code == 200, page.text
    assert page.text.count("data-calendar-section=") == 2
    assert page.text.count("data-calendar-month=") == 12
    assert page.text.count("data-day-input ") == 365
    assert (
        "Fuente y regla" not in page.text
        and "Ver evidencia de la leyenda" not in page.text
    )
    assert "data-month-step" not in page.text and 'id="calendar-month"' not in page.text
    assert "Usar reglas globales" in page.text and "Regla global v" in page.text
    assert "Previsualizar original del calendario" in page.text
    assert "Confirmar todo y dejar vigente" in page.text
    assert h == huella_calendario(conn, cvid)
    assert '<strong class="calendar-symbol-code">1</strong>' in page.text
    celda = next(
        line
        for line in page.text.splitlines()
        if line.startswith('<td data-fecha="2026-07-02"')
    )
    assert 'data-pending="false"' in celda


def test_dias_heredan_regla_global_al_guardar_sin_comentario(client, conn, calendario):
    _, cvid, _ = calendario
    h = huella_calendario(conn, cvid)
    rules = propuesta(conn, cvid)["huella"]
    op = str(uuid.uuid4())
    data = {
        "huella": h,
        "huella_reglas": rules,
        "operacion": op,
        "dia_2026-01-01": "__SIN_ASIGNAR__",
        "dia_2026-09-04": "D",
    }
    r = post(client, f"/calendarios/{cvid}/dias", data)
    assert r.status_code == 303, r.text
    nuevo = r.location.split("/")[-1].split("?")[0]
    cv = conn.execute(
        "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (nuevo,),
    ).fetchone()
    assert (
        cv["estado"] == "BORRADOR"
        and cv["clasificacion_codigos"]["D"]["es_remunerado"] is False
    )
    assert cv["clasificacion_codigos"]["G"]["es_remunerado"] is True
    assert len(cv["procedencia_extraccion"]["revision_dias"]["cambios"]) == 2
    d = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND fecha='2026-09-04'",
        (nuevo,),
    ).fetchone()
    assert d["codigo_reportado_raw"] == "L" and d["codigo_interpretado"] == "D"
    assert (
        d["evidencia_interpretacion"]["clasificacion_aceptada"]["es_remunerado"]
        is False
    )
    assert float(d["confianza"]) == 0.85 and huella_calendario(conn, cvid) == h
    assert post(client, f"/calendarios/{cvid}/dias", data).location == r.location
    assert (
        post(
            client, f"/calendarios/{cvid}/dias", {**data, "huella_reglas": "cambiada"}
        ).status_code
        == 409
    )


def test_dias_rechazan_catalogo_modificado_y_sin_guardado_parcial(
    client, conn, calendario
):
    _, cvid, _ = calendario
    h = huella_calendario(conn, cvid)
    token = propuesta(conn, cvid)["huella"]
    conn.execute("DELETE FROM leyenda_equivalencia")
    conn.commit()
    r = post(
        client,
        f"/calendarios/{cvid}/dias",
        {"huella": h, "huella_reglas": token, "dia_2026-09-04": "D"},
    )
    assert r.status_code == 409 and "reglas de código cambiaron" in r.text
    assert huella_calendario(conn, cvid) == h


def test_simbolo_global_crea_borrador_con_actividad_pago_y_original_intacto(
    client, conn, calendario
):
    _, cvid, _ = calendario
    h = huella_calendario(conn, cvid)
    rule = next(r for r in cat.catalogo(conn, "calendario") if r["nombre"] == "Gestión")
    data = {
        "codigo": "L",
        "tipo_dia": "Lectivo",
        "modo_regla": "auto",
        "categoria_global": str(rule["categoria_id"]),
        "huella_catalogo": contexto_catalogo(conn, "calendario")["huella"],
        "huella": h,
        "operacion": str(uuid.uuid4()),
    }
    r = post(client, f"/calendarios/{cvid}/leyenda", data)
    assert r.status_code == 303, r.text
    nuevo = r.location.split("/")[-1].split("?")[0]
    cv = conn.execute(
        "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (nuevo,),
    ).fetchone()
    assert cv["estado"] == "BORRADOR" and cv["creado_por"]
    assert cv["clasificacion_codigos"]["L"]["grupo_actividad"] == "GESTION"
    assert cv["clasificacion_codigos"]["L"]["es_remunerado"] is True
    assert (
        cv["clasificacion_codigos"]["L"]["categoria_general"]["id"]
        == rule["categoria_id"]
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND confianza=.85",
            (nuevo,),
        ).fetchone()["n"]
        == 365
    )
    assert huella_calendario(conn, cvid) == h
    assert post(client, f"/calendarios/{cvid}/leyenda", data).location == r.location


def test_leyenda_global_obsoleta_o_de_otro_dominio_no_guarda(client, conn, calendario):
    _, cvid, _ = calendario
    h = huella_calendario(conn, cvid)
    r = post(
        client,
        f"/calendarios/{cvid}/leyenda",
        {
            "huella": h,
            "codigo": "L",
            "tipo_dia": "Lectivo",
            "modo_regla": "auto",
            "huella_catalogo": "vencida",
        },
    )
    assert r.status_code == 409 and huella_calendario(conn, cvid) == h
    uid = conn.execute("SELECT usuario_id FROM usuario LIMIT 1").fetchone()[
        "usuario_id"
    ]
    cid = cat.guardar_categoria(
        conn,
        {
            "dominio": "asistencia",
            "nombre": "Ficticia ajena",
            "remuneracion": "SI",
            "es_falta": "NO",
            "motivo": "Regla ficticia de prueba",
        },
        uid,
        uuid.uuid4(),
    )
    conn.commit()
    r = post(
        client,
        f"/calendarios/{cvid}/leyenda",
        {
            "huella": h,
            "codigo": "L",
            "tipo_dia": "Lectivo",
            "modo_regla": "auto",
            "categoria_global": cid,
            "huella_catalogo": contexto_catalogo(conn, "calendario")["huella"],
        },
    )
    assert r.status_code == 400 and huella_calendario(conn, cvid) == h


def test_leyenda_preserva_excepcion_diaria_y_cerrada_no_se_edita(
    client, conn, calendario
):
    _, cvid, _ = calendario
    propia = {
        "tipo_dia": "Excepción ficticia",
        "grupo_actividad": "LECTIVO",
        "es_remunerado": False,
    }
    conn.execute(
        "UPDATE dia_calendarizacion SET evidencia_interpretacion=%s WHERE calendarizacion_version_id=%s AND fecha='2026-09-04'",
        (Jsonb({"clasificacion_aceptada": propia}), cvid),
    )
    conn.commit()
    r = post(
        client,
        f"/calendarios/{cvid}/leyenda",
        {
            "huella": huella_calendario(conn, cvid),
            "codigo": "L",
            "tipo_dia": "Día corregido",
            "grupo_actividad": "GESTION",
            "remuneracion": "SI",
            "modo_regla": "manual",
        },
    )
    assert r.status_code == 303, r.text
    nuevo = r.location.split("/")[-1]
    d = conn.execute(
        "SELECT evidencia_interpretacion FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND fecha='2026-09-04'",
        (nuevo,),
    ).fetchone()
    assert d["evidencia_interpretacion"]["clasificacion_aceptada"] == propia
    conn.execute(
        "UPDATE calendarizacion_version SET estado='RECHAZADA' WHERE calendarizacion_version_id=%s",
        (nuevo,),
    )
    conn.commit()
    page = client.get(f"/calendarios/{nuevo}").text
    assert "data-day-input" not in page and "data-calendar-symbol-form" not in page
    r = post(
        client,
        f"/calendarios/{nuevo}/leyenda",
        {
            "huella": huella_calendario(conn, nuevo),
            "codigo": "L",
            "tipo_dia": "Lectivo",
        },
    )
    assert r.status_code == 409
