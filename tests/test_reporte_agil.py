"""RF-F11: símbolos usados, reglas compartidas y revisión sin comentarios forzados."""

# ruff: noqa: F811
import uuid

import openpyxl
import pytest
from psycopg.types.json import Jsonb

from asistia import categorias as cat
from asistia.leyendas import codigos_usados_asistencia, normalizar_leyenda
from asistia.web.lecturas import huella_reporte
from asistia.web.reporte_reglas import contexto_catalogo

from .test_catalogo_general import asociar, crear, operador
from .test_web import app, cargar, client, fuentes, post  # noqa: F401


def escenario(client, conn, tmp_path):
    nx, cal, asistencia = fuentes(tmp_path)
    wb = openpyxl.load_workbook(asistencia)
    for columna, valor in enumerate(("A", "A", "A", None, None), start=5):
        wb.active.cell(12, columna).value = valor
    wb.save(asistencia)
    wb.close()
    for path, tipo in ((nx, "nexus"), (cal, "calendario"), (asistencia, "asistencia")):
        assert cargar(client, path, tipo).status_code == 303
    rid = conn.execute(
        "SELECT reporte_asistencia_id FROM reporte_asistencia"
    ).fetchone()["reporte_asistencia_id"]
    cats = normalizar_leyenda(
        [
            {"codigo": "A", "descripcion": "Día laborado"},
            {"codigo": "F", "descripcion": "Feriado"},
            {"codigo": "H", "descripcion": "Huelga o paro"},
            {"codigo": "T", "descripcion": "Tardanza"},
        ],
        "asistencia",
    )
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s", (Jsonb(cats),)
    )
    blanks = conn.execute(
        "SELECT asistencia_dia_id FROM asistencia_dia WHERE estado_captura='VACIO'"
    ).fetchall()
    cid = crear(conn)
    asociar(conn, cid, "Día laborado")
    conn.commit()
    return rid, cid, [str(d["asistencia_dia_id"]) for d in blanks]


def clasificar(client, conn, rid, **extra):
    return post(
        client,
        f"/reportes/{rid}/leyenda",
        {
            "codigo": "A",
            "tipo_dia": "Día laborado",
            "modo_regla": "auto",
            "huella_catalogo": contexto_catalogo(conn)["huella"],
            "huella": huella_reporte(conn, rid),
            **extra,
        },
    )


def test_leyendas_sin_uso_no_generan_pendientes_y_vacios_se_agrupan(
    client, conn, tmp_path
):
    rid, _, _ = escenario(client, conn, tmp_path)
    before = huella_reporte(conn, rid)
    html = client.get(f"/reportes/{rid}").text
    assert "2 observaciones" in html
    assert "leyendas sin uso en este reporte" not in html
    assert "Leyenda A</option>" in html
    for code in ("F", "H", "T"):
        assert f"Leyenda {code}</option>" not in html
    assert html.count('data-report-case="vacios-grupo"') == 1
    assert "data-vacios-quick" in html and "Guardar revisión" in html
    assert huella_reporte(conn, rid) == before


def test_global_completa_campos_y_copia_version_sin_comentario(client, conn, tmp_path):
    rid, cid, _ = escenario(client, conn, tmp_path)
    op = str(uuid.uuid4())
    assert clasificar(client, conn, rid, operacion=op).status_code == 303
    r = conn.execute("SELECT clasificacion_codigos FROM reporte_asistencia").fetchone()[
        "clasificacion_codigos"
    ]
    assert r["A"]["es_remunerado"] is True and r["A"]["es_falta"] is False
    assert r["A"]["categoria_general"]["id"] == cid
    assert r["A"]["categoria_general"]["version_id"]
    assert r["F"]["es_remunerado"] is None
    event = conn.execute("SELECT * FROM web_revision_evento").fetchone()
    assert "sin comentario adicional" in event["motivo"]
    assert event["usuario_id"] and event["creado_en"]
    original = event["anterior"]["clasificacion_codigos"]["A"]
    assert original["tipo_dia"] == "Día laborado" and original["es_remunerado"] is None
    assert clasificar(client, conn, rid, operacion=op).status_code == 303
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 1
    )


def test_dos_decisiones_y_guardar_revision_sin_motivos_extra(client, conn, tmp_path):
    rid, _, blanks = escenario(client, conn, tmp_path)
    assert clasificar(client, conn, rid).status_code == 303
    assert (
        post(
            client,
            f"/reportes/{rid}/vacios",
            {"dias": blanks, "huella": huella_reporte(conn, rid)},
        ).status_code
        == 303
    )
    html = client.get(f"/reportes/{rid}").text
    assert "Listo para guardar la revisión" in html, conn.execute(
        "SELECT codigo_regla,mensaje,estado FROM validacion_reporte"
    ).fetchall()
    assert "Sin pendientes de digitalización" in html
    assert "data-digitization-status>Estado: Listo para confirmar</span>" in html
    assert "Digitalización: <strong>lista para confirmar</strong>" in html
    assert html.index("data-finish-review") < html.index('class="report-grid-scroll"')
    assert html.count("data-finish-review") == 1
    assert 'data-digitization-reviewed="false"' in html
    assert (
        conn.execute("SELECT estado FROM reporte_asistencia").fetchone()["estado"]
        == "EN_VALIDACION"
    )
    response = post(
        client, f"/reportes/{rid}/revisar", {"huella": huella_reporte(conn, rid)}
    )
    assert response.status_code == 303
    assert (
        conn.execute("SELECT estado FROM reporte_asistencia").fetchone()["estado"]
        == "VALIDADO"
    )
    reviewed = client.get(f"/reportes/{rid}").text
    assert "data-digitization-status>Estado: Revisado</span>" in reviewed
    assert 'data-digitization-reviewed="true"' in reviewed
    assert "data-finish-review" not in reviewed
    # La revisión sigue siendo editable y cada cambio invalida el estado aprobado.
    assert (
        clasificar(
            client, conn, rid, modo_regla="manual", remuneracion="NO", es_falta="NO"
        ).status_code
        == 303
    )
    assert (
        conn.execute("SELECT estado FROM reporte_asistencia").fetchone()["estado"]
        == "EN_VALIDACION"
    )
    reopened = client.get(f"/reportes/{rid}").text
    assert "data-digitization-status>Estado: Listo para confirmar</span>" in reopened
    assert 'data-digitization-reviewed="false"' in reopened
    assert (
        conn.execute("SELECT count(*) n FROM web_revision_evento").fetchone()["n"] == 4
    )


def test_regla_cambiada_o_de_otro_dominio_no_se_aplica(client, conn, tmp_path):
    rid, cid, _ = escenario(client, conn, tmp_path)
    snapshot = contexto_catalogo(conn)["huella"]
    cat.guardar_categoria(
        conn,
        {
            "version": "1",
            "remuneracion": "NO",
            "es_falta": "NO",
            "motivo": "Cambio ficticio",
        },
        operador(conn),
        uuid.uuid4(),
        cid,
    )
    conn.commit()
    before = huella_reporte(conn, rid)
    assert clasificar(client, conn, rid, huella_catalogo=snapshot).status_code == 409
    calendar = crear(conn, dominio="calendario", nombre="Categoría de calendario")
    conn.commit()
    assert (
        clasificar(client, conn, rid, categoria_global=str(calendar)).status_code == 400
    )
    assert clasificar(client, conn, rid, categoria_global="999999").status_code == 400
    assert huella_reporte(conn, rid) == before


@pytest.mark.parametrize("codigo,esperado", [("F", 303), ("A", 409)])
def test_desfase_bloquea_solo_si_el_codigo_esta_en_uso(
    client, conn, tmp_path, codigo, esperado
):
    rid, _, blanks = escenario(client, conn, tmp_path)
    assert clasificar(client, conn, rid).status_code == 303
    assert (
        post(
            client,
            f"/reportes/{rid}/vacios",
            {"dias": blanks, "huella": huella_reporte(conn, rid)},
        ).status_code
        == 303
    )
    cats = conn.execute(
        "SELECT clasificacion_codigos FROM reporte_asistencia"
    ).fetchone()["clasificacion_codigos"]
    cats[codigo]["revision_leyenda"] = {
        "estado": "PENDIENTE",
        "motivo": "Desfase ficticio",
    }
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s", (Jsonb(cats),)
    )
    conn.execute(
        "INSERT INTO validacion_reporte(reporte_asistencia_id,codigo_regla,severidad,mensaje,evidencia) VALUES(%s,'LEYENDA_POSIBLE_DESFASE','ERROR','Desfase ficticio',%s)",
        (rid, Jsonb({codigo: cats[codigo]["revision_leyenda"]})),
    )
    conn.commit()
    response = post(
        client, f"/reportes/{rid}/revisar", {"huella": huella_reporte(conn, rid)}
    )
    assert response.status_code == esperado, response.text
    alerta = conn.execute("SELECT * FROM validacion_reporte").fetchone()
    assert alerta["estado"] == ("DESCARTADA" if esperado == 303 else "PENDIENTE")
    if esperado == 303:
        evento = conn.execute(
            "SELECT anterior,posterior FROM web_revision_evento WHERE accion='REVISAR_REPORTE'"
        ).fetchone()
        assert evento["anterior"]["alertas_leyenda"][0]["estado"] == "PENDIENTE"
        assert evento["posterior"]["alertas_leyenda"][0]["estado"] == "DESCARTADA"


def test_otra_observacion_critica_conserva_bloqueo(client, conn, tmp_path):
    rid, _, _ = escenario(client, conn, tmp_path)
    conn.execute(
        "INSERT INTO validacion_reporte(reporte_asistencia_id,codigo_regla,severidad,mensaje) VALUES(%s,'ASISTENCIA_CONTRADICTORIA','ERROR','Contradicción ficticia')",
        (rid,),
    )
    conn.commit()
    response = post(
        client, f"/reportes/{rid}/revisar", {"huella": huella_reporte(conn, rid)}
    )
    assert response.status_code == 409
    assert "1 observaciones críticas" in response.text
    assert (
        conn.execute("SELECT estado FROM validacion_reporte").fetchone()["estado"]
        == "PENDIENTE"
    )


def test_comentario_explicito_y_limite_se_conservan(client, conn, tmp_path):
    rid, _, _ = escenario(client, conn, tmp_path)
    assert clasificar(client, conn, rid, motivo="x" * 4001).status_code == 400
    assert (
        clasificar(
            client, conn, rid, motivo="Excepción que el usuario decidió explicar."
        ).status_code
        == 303
    )
    assert (
        conn.execute("SELECT motivo FROM web_revision_evento").fetchone()["motivo"]
        == "Excepción que el usuario decidió explicar."
    )


def test_codigos_por_leer_se_conservan_y_no_aplica_no_activa_leyenda():
    assert codigos_usados_asistencia(
        [
            {"estado_captura": "PENDIENTE", "codigo_reportado_raw": "X"},
            {"estado_captura": "DERIVADO", "codigo_interpretado": "A"},
            {"estado_captura": "NO_APLICA", "codigo_reportado_raw": "F"},
            {"estado_captura": "VACIO", "codigo_reportado_raw": None},
        ]
    ) == {"X", "A"}
