"""RF-F02: detalle institucional y previsualización; base aislada, datos ficticios."""
# ruff: noqa: F811

from datetime import date

import pytest
from psycopg.types.json import Jsonb

from asistia.web.instituciones import calendario_institucion, reportes_institucion

from .test_instituciones import documento, institucion
from .test_web import app, client  # noqa: F401


def reporte(conn, iid, periodo, *, serie=None, version=1, estado="IMPORTADO"):
    did = documento(conn, "2026-09-15 12:00+00")
    return conn.execute(
        """INSERT INTO reporte_asistencia(institucion_educativa_id,documento_recibido_id,periodo,tipo_fuente,institucion_reportada_raw,reporte_asistencia_serie_id,version,estado)
      VALUES (%s,%s,%s,'EXCEL_NATIVO','Institución ficticia',%s,%s,%s) RETURNING reporte_asistencia_id""",
        (iid, did, periodo, serie, version, estado),
    ).fetchone()["reporte_asistencia_id"]


def calendario(conn, iid, *, anio=2026, version=1, estado="BORRADOR"):
    local = conn.execute(
        """INSERT INTO calendarizacion_local(institucion_educativa_id,anio) VALUES (%s,%s)
      ON CONFLICT(institucion_educativa_id,anio) DO UPDATE SET anio=excluded.anio RETURNING calendarizacion_local_id""",
        (iid, anio),
    ).fetchone()["calendarizacion_local_id"]
    uid = conn.execute(
        "SELECT usuario_id FROM usuario ORDER BY usuario_id LIMIT 1"
    ).fetchone()["usuario_id"]
    cvid = conn.execute(
        """INSERT INTO calendarizacion_version(calendarizacion_local_id,version,origen,estado,motivo_version,aprobado_por,aprobado_en,clasificacion_codigos)
      VALUES (%s,%s,'CREACION_MANUAL',%s,'Calendario ficticio',%s,CASE WHEN %s='VIGENTE' THEN now() ELSE NULL END,%s) RETURNING calendarizacion_version_id""",
        (
            local,
            version,
            estado,
            uid if estado == "VIGENTE" else None,
            estado,
            Jsonb(
                {
                    "L": {
                        "tipo_dia": "Día lectivo ficticio",
                        "grupo_actividad": "LECTIVO",
                        "es_remunerado": True,
                        "fuente": "LEYENDA_DOCUMENTAL",
                    }
                }
            ),
        ),
    ).fetchone()["calendarizacion_version_id"]
    conn.execute(
        """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,codigo_reportado_raw,estado_captura,hoja_origen,celda_origen)
      VALUES (%s,%s,(SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno='LECTIVO'),'L','REGISTRADO','Hoja ficticia','B8')""",
        (cvid, date(anio, 7, 1)),
    )
    return cvid


def test_reportes_todos_los_meses_y_nulos_no_desaparecen(client, conn):
    iid = institucion(conn)
    otro = institucion(conn, "9999002")
    mayo = reporte(conn, iid, "2026-05-01")
    julio = reporte(conn, iid, "2026-07-01")
    repetido = reporte(conn, iid, "2026-07-01")
    ajeno = reporte(conn, otro, "2026-08-01")
    conn.commit()
    rows = reportes_institucion(conn, iid)
    assert {r["reporte_asistencia_id"] for r in rows} == {mayo, julio, repetido}
    html = client.get(f"/instituciones/{iid}?periodo=2026-09").text
    assert (
        html.index('id="reportes-titulo"')
        < html.index('id="personal-titulo"')
        < html.index('id="calendario-institucion-titulo"')
    )
    assert (
        str(mayo) in html
        and str(julio) in html
        and str(repetido) in html
        and str(ajeno) not in html
    )
    assert (
        "No hay personal registrado" in html and "No hay un calendario actual" in html
    )


def test_ultima_version_por_serie_incluye_estado_rechazado(client, conn):
    iid = institucion(conn)
    serie = conn.execute(
        "INSERT INTO reporte_asistencia_serie(institucion_educativa_id,periodo,nivel_modalidad,turno) VALUES (%s,'2026-07-01','PRIMARIA','Mañana') RETURNING reporte_asistencia_serie_id",
        (iid,),
    ).fetchone()["reporte_asistencia_serie_id"]
    reporte(conn, iid, "2026-07-01", serie=serie)
    ultima = reporte(
        conn, iid, "2026-07-01", serie=serie, version=2, estado="RECHAZADO"
    )
    filas = reportes_institucion(conn, iid)
    assert len(filas) == 1 and filas[0]["reporte_asistencia_id"] == ultima
    assert filas[0]["versiones"] == 2 and filas[0]["estado"] == "RECHAZADO"


def test_calendario_vigente_preferido_y_edicion_del_borrador_posterior(client, conn):
    iid = institucion(conn)
    vigente = calendario(conn, iid, estado="VIGENTE")
    borrador = calendario(conn, iid, version=2)
    conn.commit()
    d = calendario_institucion(conn, iid)
    assert d["calendario"]["calendarizacion_version_id"] == vigente
    assert d["edicion_calendario"]["calendarizacion_version_id"] == borrador
    html = client.get(f"/instituciones/{iid}?q=Ficticio&nivel_ie=PRIMARIA").text
    assert "Calendarización vigente" in html and "Hay una versión 2 posterior" in html
    assert f"/calendarios/{borrador}?mes=7#dias" in html
    assert f"/calendarios/{vigente}?mes=7#leyenda" in html
    assert "data-day-input" not in html and "data-calendar-save" not in html
    assert (
        'class="annual-calendar"' in html
        and "data-calendar-confirm" in html
        and "disabled>Confirmar todo y dejar vigente" in html
    )
    assert 'data-year="2026"' in html and "nivel_ie=PRIMARIA" in html
    assert "Día lectivo ficticio" in html and "Sin registro" in html
    assert "data-day-input" in client.get(f"/calendarios/{borrador}").text


def test_calendario_propio_ultimo_anio_y_consulta_historica_no_cambian_datos(
    client, conn
):
    iid = institucion(conn)
    anterior = calendario(conn, iid, anio=2025, estado="VIGENTE")
    actual = calendario(conn, iid)
    ajeno = calendario(conn, institucion(conn, "9999002"), version=1)
    conn.commit()
    d = calendario_institucion(conn, iid)
    assert d["calendario"]["calendarizacion_version_id"] == actual
    assert d["anios_calendario"] == [2026, 2025]
    assert (
        calendario_institucion(conn, iid, anio=2025)["calendario"][
            "calendarizacion_version_id"
        ]
        == anterior
    )
    before = conn.execute(
        "SELECT to_jsonb(cv) dato FROM calendarizacion_version cv ORDER BY calendarizacion_version_id"
    ).fetchall()
    html = client.get(f"/instituciones/{iid}?anio_calendario=2025&mes=8").text
    assert "Calendario anual 2025" in html and str(ajeno) not in html
    after = conn.execute(
        "SELECT to_jsonb(cv) dato FROM calendarizacion_version cv ORDER BY calendarizacion_version_id"
    ).fetchall()
    assert before == after
    html = client.get(f"/instituciones/{iid}").text
    assert "pendiente de aprobación" in html


@pytest.mark.parametrize(
    "query", ["anio_calendario=2024", "anio_calendario=abc", "mes=13", "mes=0"]
)
def test_calendario_institucional_rechaza_ambito_invalido(client, conn, query):
    iid = institucion(conn)
    calendario(conn, iid)
    conn.commit()
    assert client.get(f"/instituciones/{iid}?{query}").status_code == 400


def test_ultima_rechazada_no_ofrece_editar_una_version_obsoleta(client, conn):
    iid = institucion(conn)
    vigente = calendario(conn, iid, estado="VIGENTE")
    rechazada = calendario(conn, iid, version=2, estado="RECHAZADA")
    conn.commit()
    d = calendario_institucion(conn, iid)
    assert d["calendario"]["calendarizacion_version_id"] == vigente
    assert d["edicion_calendario"]["calendarizacion_version_id"] == rechazada
    html = client.get(f"/instituciones/{iid}").text
    assert "Consultar última versión</a>" in html
    assert "data-day-input" not in client.get(f"/calendarios/{rechazada}").text
