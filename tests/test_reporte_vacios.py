"""Selección explícita de vacíos: atomicidad, alcance y trazabilidad."""

# ruff: noqa: F811
import uuid

import pytest

from asistia.consolidado.remuneracion import cruce_persona
from asistia.web.lecturas import huella_reporte

from .test_web import app, ciclo, client, post  # noqa: F401


def preparar(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    dias = conn.execute(
        "SELECT * FROM asistencia_dia ORDER BY fecha LIMIT 3"
    ).fetchall()
    ids = [d["asistencia_dia_id"] for d in dias]
    conn.execute(
        "UPDATE asistencia_dia SET estado_captura='VACIO',estado_asistencia_id=NULL,"
        "codigo_reportado_raw=NULL,codigo_interpretado=NULL,hecho_asistencia_dia_id=NULL,"
        "evidencia_interpretacion='{}',validado=false WHERE asistencia_dia_id=ANY(%s)",
        (ids,),
    )
    conn.commit()
    return rid, ids


def payload(conn, rid, ids):
    return {
        "dias": [str(i) for i in ids],
        "huella": huella_reporte(conn, rid),
        "motivo": "Fechas sin actividad cotejadas con el calendario ficticio.",
        "operacion": str(uuid.uuid4()),
    }


def test_grupo_conserva_original_no_crea_faltas_y_se_puede_rectificar(
    client, conn, tmp_path
):
    rid, ids = preparar(client, conn, tmp_path)
    originales = conn.execute(
        "SELECT * FROM asistencia_dia ORDER BY asistencia_dia_id"
    ).fetchall()
    token = huella_reporte(conn, rid)
    html = client.get(f"/reportes/{rid}").text
    assert "Seleccionar vacíos (3)" in html and "data-vacios-date" in html
    assert huella_reporte(conn, rid) == token
    response = post(client, f"/reportes/{rid}/vacios", payload(conn, rid, ids[:2]))
    assert response.status_code == 303
    actuales = conn.execute(
        "SELECT * FROM asistencia_dia ORDER BY asistencia_dia_id"
    ).fetchall()
    for antes, despues in zip(originales, actuales):
        if antes["asistencia_dia_id"] not in ids[:2]:
            assert antes == despues
            continue
        assert despues["estado_captura"] == "NO_APLICA" and despues["validado"]
        for campo in (
            "codigo_reportado_raw",
            "celda_origen",
            "es_dia_laborable_esperado",
        ):
            assert despues[campo] == antes[campo]
        assert (
            despues["estado_asistencia_id"] is None
            and despues["codigo_interpretado"] is None
        )
        assert despues["evidencia_interpretacion"]["no_correspondia_asistir"] is True
    evento = conn.execute("SELECT * FROM web_revision_evento").fetchone()
    assert len(evento["anterior"]["dias"]) == len(evento["posterior"]["dias"]) == 2
    assert {d["estado_captura"] for d in evento["anterior"]["dias"]} == {"VACIO"}
    assert evento["motivo"] and evento["usuario_id"]
    html = client.get(response.location).text
    assert (
        "Vacíos revisados en grupo" in html
        and "2 vacíos → No correspondía asistir" in html
    )
    assert "Seleccionar vacíos (1)" in html
    assert (
        conn.execute("SELECT estado FROM reporte_asistencia").fetchone()["estado"]
        == "EN_VALIDACION"
    )
    cruce = cruce_persona(conn, originales[0]["trabajador_en_reporte_id"])
    for d in cruce["dias"][:2]:
        assert d["es_falta"] is None and d["es_remunerado"] is None
    # Recuperación por el editor individual, conservando también la decisión en grupo.
    r = post(
        client,
        f"/reportes/{rid}/dias/{ids[0]}",
        {
            "captura": "VACIO",
            "codigo": "",
            "motivo": "Se retira la interpretación ficticia.",
            "huella": huella_reporte(conn, rid),
            "volver": "reporte",
        },
    )
    assert r.status_code == 303
    assert (
        conn.execute(
            "SELECT estado_captura FROM asistencia_dia WHERE asistencia_dia_id=%s",
            (ids[0],),
        ).fetchone()["estado_captura"]
        == "VACIO"
    )
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 2
    )


def test_reenvio_identico_sin_duplicar_y_reuso_distinto_rechazado(
    client, conn, tmp_path
):
    rid, ids = preparar(client, conn, tmp_path)
    data = payload(conn, rid, ids)
    url = f"/reportes/{rid}/vacios"
    assert post(client, url, data, headers={"X-ASISTIA-Async": "1"}).json == {
        "redirect": f"/reportes/{rid}"
    }
    assert (
        post(client, url, {**data, "dias": list(reversed(data["dias"]))}).status_code
        == 303
    )
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 1
    )
    assert post(client, url, {**data, "dias": data["dias"][:1]}).status_code == 409


@pytest.mark.parametrize(
    "seleccion,codigo",
    [
        ("vacia", 400),
        ("malformada", 400),
        ("ajena", 409),
        ("marca", 409),
        ("pendiente", 409),
    ],
)
def test_seleccion_invalida_no_guarda_parcialmente(
    client, conn, tmp_path, seleccion, codigo
):
    rid, ids = preparar(client, conn, tmp_path)
    selected = ids[:1]
    if seleccion == "vacia":
        selected = []
    elif seleccion == "malformada":
        selected += ["no-es-uuid"]
    elif seleccion == "ajena":
        selected += [uuid.uuid4()]
    elif seleccion == "marca":
        selected += [
            conn.execute(
                "SELECT asistencia_dia_id FROM asistencia_dia WHERE estado_captura='REGISTRADO' LIMIT 1"
            ).fetchone()["asistencia_dia_id"]
        ]
    else:
        conn.execute(
            "UPDATE asistencia_dia SET estado_captura='PENDIENTE' WHERE asistencia_dia_id=%s",
            (ids[1],),
        )
        conn.commit()
        selected += [ids[1]]
    before = huella_reporte(conn, rid)
    response = post(client, f"/reportes/{rid}/vacios", payload(conn, rid, selected))
    assert response.status_code == codigo
    assert huella_reporte(conn, rid) == before
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 0
    )


def test_reporte_cambiado_y_version_historica_no_admiten_lote(client, conn, tmp_path):
    rid, ids = preparar(client, conn, tmp_path)
    data = payload(conn, rid, ids)
    conn.execute(
        "UPDATE asistencia_dia SET observacion='Corrección de otro revisor' WHERE asistencia_dia_id=%s",
        (ids[0],),
    )
    conn.commit()
    assert post(client, f"/reportes/{rid}/vacios", data).status_code == 409
    conn.execute(
        "INSERT INTO reporte_asistencia(documento_recibido_id,reporte_asistencia_serie_id,institucion_educativa_id,periodo,hoja_pagina_origen,indice_bloque,version,estado,tipo_fuente,institucion_reportada_raw) SELECT documento_recibido_id,reporte_asistencia_serie_id,institucion_educativa_id,periodo,hoja_pagina_origen,indice_bloque,version+1,'IMPORTADO',tipo_fuente,institucion_reportada_raw FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    conn.commit()
    assert (
        post(client, f"/reportes/{rid}/vacios", payload(conn, rid, ids)).status_code
        == 409
    )
    assert "data-vacios-form" not in client.get(f"/reportes/{rid}").text
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 0
    )


def test_comentario_opcional_y_csrf_obligatorio(client, conn, tmp_path):
    rid, ids = preparar(client, conn, tmp_path)
    data = payload(conn, rid, ids)
    assert client.post(f"/reportes/{rid}/vacios", data=data).status_code == 400
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 0
    )
    assert (
        post(client, f"/reportes/{rid}/vacios", {**data, "motivo": ""}).status_code
        == 303
    )
    assert (
        "sin comentario adicional"
        in conn.execute("SELECT motivo FROM web_revision_evento").fetchone()["motivo"]
    )
