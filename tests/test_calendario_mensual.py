"""Asignación explícita de días, versiones y navegación; solo PostgreSQL ficticio."""
# ruff: noqa: F811

import uuid

import pytest
from psycopg.types.json import Jsonb

from asistia.web.leyendas import huella_calendario

from .test_leyendas_remuneracion import preparar_cruce
from .test_web import app, client, post  # noqa: F401


@pytest.fixture
def calendario(conn, tmp_path, client):
    conn.execute("SET TIME ZONE 'America/Lima'")
    _, cvid, _ = preparar_cruce(conn, tmp_path)
    conn.execute(
        "UPDATE dia_calendarizacion SET confianza=0.8500 WHERE calendarizacion_version_id=%s",
        (cvid,),
    )
    conn.commit()
    return cvid


def enviar(client, conn, cvid, cambios, **extra):
    return post(
        client,
        f"/calendarios/{cvid}/dias",
        {
            "huella": huella_calendario(conn, cvid),
            "motivo": "Cotejo ficticio del calendario",
            "mes": "7",
            **{f"dia_{f}": c for f, c in cambios.items()},
            **extra,
        },
    )


def test_guardado_conserva_fuente_y_dias_crea_borrador_idempotente(
    client, conn, calendario
):
    cvid = calendario
    antes = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (cvid,),
    ).fetchall()
    h = huella_calendario(conn, cvid)
    op = str(uuid.uuid4())
    r = enviar(
        client, conn, cvid, {"2026-07-01": "D", "2026-08-02": "U1"}, operacion=op
    )
    assert r.status_code == 303, r.text
    nuevo = r.location.split("/")[-1].split("?")[0]
    cv = conn.execute(
        "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (nuevo,),
    ).fetchone()
    assert cv["estado"] == "BORRADOR" and cv["aprobado_por"] is None
    assert str(cv["version_padre_id"]) == str(cvid)
    assert cv["creado_por"] is not None
    assert huella_calendario(conn, cvid) == h
    dias = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (nuevo,),
    ).fetchall()
    assert len(dias) == len(antes) + 1
    assert dias[0]["codigo_reportado_raw"] == antes[0]["codigo_reportado_raw"]
    assert dias[0]["codigo_interpretado"] == "D"
    assert (
        dias[0]["evidencia_interpretacion"]["clasificacion_aceptada"]["es_remunerado"]
        is False
    )
    for original, copia in zip(antes[1:], dias[1:-1]):
        for k in original.keys() - {
            "dia_calendarizacion_id",
            "calendarizacion_version_id",
        }:
            assert original[k] == copia[k], k
    assert dias[-1]["codigo_reportado_raw"] is None
    assert dias[-1]["evidencia_interpretacion"]["revision_dia"]["anterior"] is None
    repetido = enviar(
        client,
        conn,
        cvid,
        {"2026-07-01": "D", "2026-08-02": "U1"},
        operacion=op,
        huella=h,
    )
    assert repetido.location == r.location
    assert (
        enviar(client, conn, cvid, {"2026-07-02": "D"}, operacion=op).status_code == 409
    )
    assert enviar(client, conn, cvid, {"2026-07-02": "D"}).status_code == 409
    assert (
        conn.execute("SELECT count(*) n FROM calendarizacion_version").fetchone()["n"]
        == 2
    )


@pytest.mark.parametrize(
    "cambios,extra",
    [
        ({"2026-07-01": "INEXISTENTE"}, {}),
        ({"2025-07-01": "D"}, {}),
        ({"2026-02-30": "D"}, {}),
        ({"20260701": "D"}, {}),
        ({"2026-07-01": "D"}, {"motivo": "x" * 4001}),
        ({}, {}),
    ],
)
def test_error_no_guarda_parcialmente(client, conn, calendario, cambios, extra):
    h = huella_calendario(conn, calendario)
    assert enviar(client, conn, calendario, cambios, **extra).status_code == 400
    assert huella_calendario(conn, calendario) == h
    assert (
        conn.execute("SELECT count(*) n FROM calendarizacion_version").fetchone()["n"]
        == 1
    )


def test_formulario_obsoleto_y_csrf(client, conn, calendario):
    assert (
        enviar(
            client, conn, calendario, {"2026-07-01": "D"}, huella="vieja"
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/calendarios/{calendario}/dias", data={"dia_2026-07-01": "D"}
        ).status_code
        == 400
    )


def test_revision_diaria_se_conserva_al_reaplicar_leyenda(client, conn, calendario):
    from asistia.leyendas import aplicar_leyenda_calendario, categoria_dia

    r = enviar(client, conn, calendario, {"2026-07-01": "D"})
    assert r.status_code == 303
    nuevo = r.location.split("/")[-1].split("?")[0]
    cv = conn.execute(
        "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (nuevo,),
    ).fetchone()
    nuevas = {
        **cv["clasificacion_codigos"],
        "D": {
            "tipo_dia": "Otra regla",
            "grupo_actividad": "GESTION",
            "es_remunerado": True,
        },
    }
    aplicar_leyenda_calendario(conn, nuevo, nuevas, usar_catalogo=False)
    d = conn.execute(
        "SELECT d.*,t.codigo_interno FROM dia_calendarizacion d JOIN catalogo_tipo_dia t USING(tipo_dia_id) WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
        (nuevo,),
    ).fetchone()
    assert d["codigo_interno"] == "NO_LECTIVO_NI_GESTION"
    assert categoria_dia({"clasificacion_codigos": nuevas}, d)["es_remunerado"] is False


def test_derivado_permanece_derivado_sin_aprobacion(client, conn, calendario):
    conn.execute(
        "UPDATE calendarizacion_version SET procedencia_extraccion=procedencia_extraccion||%s WHERE calendarizacion_version_id=%s",
        (Jsonb({"tipo": "DERIVADO_2025"}), calendario),
    )
    conn.commit()
    r = enviar(client, conn, calendario, {"2026-07-01": "D"})
    assert r.status_code == 303
    nuevo = r.location.split("/")[-1].split("?")[0]
    assert post(client, f"/calendarios/{nuevo}/aprobar").status_code == 409


def test_vista_anual_sin_inferencias_y_mes_acotado(client, conn, calendario):
    r = client.get(f"/calendarios/{calendario}?mes=7")
    assert r.status_code == 200
    assert 'data-calendar-month="7"' in r.text
    assert r.text.count("data-day-input") == 365
    assert 'data-fecha="2026-08-02"' in r.text
    assert "Sin registro" in r.text
    assert "Guardar cambios de días" in r.text
    assert client.get(f"/calendarios/{calendario}?mes=13").status_code == 400


def test_anio_bisiesto_y_dias_semana(conn):
    from asistia.web.calendario_mensual import meses_calendario

    meses = meses_calendario({"anio": 2024, "clasificacion_codigos": {}}, [])
    febrero = [c for s in meses[1]["semanas"] for c in s if c]
    assert len(febrero) == 29
    assert meses[1]["semanas"][0][4]["fecha"].isoformat() == "2024-02-01"
    assert all(c["pendiente"] and not c["inicial"] for c in febrero)


def test_formdata_del_anio_completo_y_lectura_anteriores(client, conn, calendario):
    import re

    html = client.get(f"/calendarios/{calendario}?mes=7").text
    datos = {nombre: "" for nombre in re.findall(r'name="(dia_[\d-]+)"', html)}
    assert len(datos) == 365
    datos.update(
        {
            "dia_2026-07-01": "D",
            "huella": huella_calendario(conn, calendario),
            "motivo": "Comprobación multipart del año completo",
            "mes": "7",
        }
    )
    r = post(
        client,
        f"/calendarios/{calendario}/dias",
        datos,
        content_type="multipart/form-data",
    )
    assert r.status_code == 303
    anterior = client.get(f"/calendarios/{calendario}").text
    assert "data-day-input" not in anterior and "Abrir versión 2" in anterior


def test_reenvio_original_tras_otra_version_de_leyenda(client, conn, calendario):
    from asistia.web.leyendas import guardar_calendario

    op = str(uuid.uuid4())
    r = enviar(client, conn, calendario, {"2026-07-01": "D"}, operacion=op)
    assert r.status_code == 303
    nuevo = r.location.split("/")[-1].split("?")[0]
    uid = conn.execute(
        "SELECT creado_por FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (nuevo,),
    ).fetchone()["creado_por"]
    otro = guardar_calendario(
        conn,
        nuevo,
        {
            "codigo": "D",
            "tipo_dia": "Descanso",
            "remuneracion": "SI",
            "grupo_actividad": "NO_LECTIVO_NI_GESTION",
        },
        "Otra leyenda ficticia",
        huella_calendario(conn, nuevo),
        uuid.uuid4(),
        uid,
    )
    conn.commit()
    repetido = enviar(client, conn, calendario, {"2026-07-01": "D"}, operacion=op)
    assert repetido.location == r.location
    d = conn.execute(
        "SELECT evidencia_interpretacion FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
        (otro,),
    ).fetchone()
    assert (
        d["evidencia_interpretacion"]["clasificacion_aceptada"]["es_remunerado"]
        is False
    )


def test_asignar_sin_cambio_no_crea_version(client, conn, calendario):
    r = enviar(client, conn, calendario, {"2026-07-01": "U1"})
    assert r.status_code == 400
    assert "No hay cambios" in r.text
    assert (
        conn.execute("SELECT count(*) n FROM calendarizacion_version").fetchone()["n"]
        == 1
    )


def test_asignacion_explicita_repara_actividad_diferente_sin_tocar_raw(
    client, conn, calendario
):
    conn.execute(
        "UPDATE dia_calendarizacion SET tipo_dia_id=(SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno='GESTION') WHERE calendarizacion_version_id=%s AND fecha='2026-07-04'",
        (calendario,),
    )
    conn.commit()
    r = enviar(client, conn, calendario, {"2026-07-04": "D"})
    assert r.status_code == 303
    nuevo = r.location.split("/")[-1].split("?")[0]
    d = conn.execute(
        "SELECT d.codigo_reportado_raw,t.codigo_interno FROM dia_calendarizacion d JOIN catalogo_tipo_dia t USING(tipo_dia_id) WHERE calendarizacion_version_id=%s AND fecha='2026-07-04'",
        (nuevo,),
    ).fetchone()
    assert d == {"codigo_reportado_raw": "D", "codigo_interno": "NO_LECTIVO_NI_GESTION"}


def test_sin_asignar_mes_conserva_fuente_historia_y_reenvio(client, conn, calendario):
    from asistia.leyendas import aplicar_leyenda_calendario, categoria_dia
    from asistia.web.calendario_anual import calendario_anual
    from asistia.web.calendario_mensual import meses_calendario

    conn.execute(
        """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,
        estado_captura,codigo_reportado_raw,codigo_interpretado,hoja_origen,celda_origen,confianza)
        SELECT %s,'2026-01-01'::date+n,tipo_dia_id,'REGISTRADO','OCR G','U1',
        'Fuente ficticia','enero día '||(n+1),0.85
        FROM generate_series(0,30) n CROSS JOIN catalogo_tipo_dia WHERE codigo_interno='GESTION'""",
        (calendario,),
    )
    conn.commit()
    originales = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (calendario,),
    ).fetchall()
    h = huella_calendario(conn, calendario)
    cambios = {f"2026-01-{n:02}": "__SIN_ASIGNAR__" for n in range(1, 32)}
    op = str(uuid.uuid4())
    r = enviar(client, conn, calendario, cambios, operacion=op, mes="1")
    assert r.status_code == 303, r.text
    nuevo = r.location.split("/")[-1].split("?")[0]
    assert (
        enviar(
            client, conn, calendario, cambios, operacion=op, huella=h, mes="1"
        ).location
        == r.location
    )
    cv = conn.execute(
        "SELECT cv.*,cl.anio FROM calendarizacion_version cv JOIN calendarizacion_local cl USING(calendarizacion_local_id) WHERE calendarizacion_version_id=%s",
        (nuevo,),
    ).fetchone()
    assert cv["estado"] == "BORRADOR" and cv["aprobado_por"] is None
    assert str(cv["version_padre_id"]) == str(calendario)
    assert len(cv["procedencia_extraccion"]["revision_dias"]["cambios"]) == 31
    assert huella_calendario(conn, calendario) == h
    aplicar_leyenda_calendario(
        conn, nuevo, cv["clasificacion_codigos"], usar_catalogo=False
    )
    copias = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (nuevo,),
    ).fetchall()
    for a, d in zip(originales, copias, strict=True):
        if d["fecha"].month != 1:
            assert all(
                a[k] == d[k]
                for k in a.keys()
                - {"dia_calendarizacion_id", "calendarizacion_version_id"}
            )
            continue
        assert d["estado_captura"] == "SIN_ASIGNAR"
        assert d["tipo_dia_id"] is None and d["codigo_interpretado"] is None
        for k in ("codigo_reportado_raw", "hoja_origen", "celda_origen", "confianza"):
            assert a[k] == d[k]
        assert categoria_dia(cv, d) == {
            "tipo_dia": "Sin asignar",
            "grupo_actividad": None,
            "es_remunerado": None,
        }
        revision = d["evidencia_interpretacion"]["revision_dia"]
        assert revision["anterior"]["codigo_interpretado"] == "U1"
        assert revision["autor"] == cv["creado_por"] and revision["motivo"]
    conn.commit()
    enero = calendario_anual(cv, copias)["meses"][0]
    assert enero["conteos"][""] == 31 and enero["conteos"]["GESTION"] == 0
    assert all(
        c["simbolo"] == "∅" and "Sin asignar" in c["detalle"]
        for c in enero["celdas"]
        if c
    )
    celdas = [c for s in meses_calendario(cv, copias)[0]["semanas"] for c in s if c]
    assert all(c["inicial"] == "__SIN_ASIGNAR__" and not c["grupo"] for c in celdas)
    assert enviar(client, conn, nuevo, cambios).status_code == 400
    assert post(client, f"/calendarios/{nuevo}/aprobar").status_code == 303


def test_sin_asignar_pendiente_fuera_enero_y_reasignacion(client, conn, calendario):
    from asistia.consolidado.remuneracion import cruce_persona

    r = enviar(
        client,
        conn,
        calendario,
        {"2026-07-01": "__SIN_ASIGNAR__", "2026-08-02": "__SIN_ASIGNAR__"},
    )
    assert r.status_code == 303, r.text
    nuevo = r.location.split("/")[-1].split("?")[0]
    assert post(client, f"/calendarios/{nuevo}/aprobar").status_code == 409
    tid = conn.execute(
        "SELECT trabajador_en_reporte_id FROM trabajador_en_reporte"
    ).fetchone()["trabajador_en_reporte_id"]
    dia = cruce_persona(conn, tid, calendario_id=nuevo)["dias"][0]
    assert dia["resultado"] == "PENDIENTE" and dia["es_remunerado"] is None
    r = enviar(client, conn, nuevo, {"2026-07-01": "U1", "2026-08-02": "D"})
    assert r.status_code == 303
    reasignado = r.location.split("/")[-1].split("?")[0]
    dias = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND fecha IN ('2026-07-01','2026-08-02') ORDER BY fecha",
        (reasignado,),
    ).fetchall()
    assert all(d["estado_captura"] == "REGISTRADO" and d["tipo_dia_id"] for d in dias)
    assert (
        dias[0]["evidencia_interpretacion"]["revision_dia"]["anterior"][
            "estado_captura"
        ]
        == "SIN_ASIGNAR"
    )
    assert dias[1]["codigo_reportado_raw"] is None


def test_sin_asignar_disponible_sin_leyenda(client, conn, calendario):
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos='{}' WHERE calendarizacion_version_id=%s",
        (calendario,),
    )
    conn.commit()
    html = client.get(f"/calendarios/{calendario}").text
    assert 'value="__SIN_ASIGNAR__"' in html
    assert (
        enviar(client, conn, calendario, {"2026-07-01": "__SIN_ASIGNAR__"}).status_code
        == 303
    )


def test_sin_asignar_no_admite_actividad_en_base(conn, calendario):
    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation), conn.transaction():
        conn.execute(
            "UPDATE dia_calendarizacion SET estado_captura='SIN_ASIGNAR' WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
            (calendario,),
        )
