"""La interpretación previa de una fila no constituye otra fuente contradictoria."""

# ruff: noqa: F811
from asistia.leyendas import aplicar_leyenda_asistencia

from .test_web import app, ciclo, client  # noqa: F401


def reclasificar(conn, rid, codigo, estado):
    cats = conn.execute(
        "SELECT clasificacion_codigos FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
        (rid,),
    ).fetchone()["clasificacion_codigos"]
    cats[codigo] = {
        "tipo_dia": "Lectura cotejada",
        "estado_asistencia_codigo": estado,
        "fuente": "REVISION_WEB",
        "es_falta": estado == "I",
        "es_remunerado": estado == "A",
    }
    aplicar_leyenda_asistencia(conn, rid, cats, usar_catalogo=False)
    conn.commit()


def alertas(conn):
    return conn.execute(
        "SELECT * FROM validacion_reporte WHERE codigo_regla='ASISTENCIA_CONTRADICTORIA'"
    ).fetchall()


def test_corregir_misma_fuente_no_fabrica_conflictos(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    original = conn.execute(
        "SELECT asistencia_dia_id,codigo_reportado_raw FROM asistencia_dia WHERE fecha='2026-07-03'"
    ).fetchone()
    for estado in ["A", "A", "I", "A"]:
        reclasificar(conn, rid, "I", estado)
        actual = conn.execute(
            "SELECT a.codigo_reportado_raw,c.codigo,h.es_disputado FROM asistencia_dia a JOIN hecho_asistencia_dia h USING(hecho_asistencia_dia_id) JOIN catalogo_estado_asistencia c ON c.estado_asistencia_id=h.estado_asistencia_id WHERE a.asistencia_dia_id=%s",
            (original["asistencia_dia_id"],),
        ).fetchone()
        assert actual == {
            "codigo_reportado_raw": "I",
            "codigo": estado,
            "es_disputado": False,
        }
        assert not alertas(conn)


def test_otra_fuente_contradictoria_se_conserva_sin_multiplicar_alertas(
    client, conn, tmp_path
):
    _rid, _ = ciclo(client, conn, tmp_path)
    # Otra fila documental del mismo reporte/persona/rol, con localizador distinto.
    tid = conn.execute("""INSERT INTO trabajador_en_reporte(reporte_asistencia_id,trabajador_id,rol_laboral_id,vinculo_trabajador_ie_id,dni_reportado_raw,nombres_reportados_raw,cargo_reportado_raw,fila_detalle_origen,estado_match)
        SELECT reporte_asistencia_id,trabajador_id,rol_laboral_id,vinculo_trabajador_ie_id,dni_reportado_raw,nombres_reportados_raw,cargo_reportado_raw,50,estado_match FROM trabajador_en_reporte LIMIT 1 RETURNING trabajador_en_reporte_id""").fetchone()[
        "trabajador_en_reporte_id"
    ]
    aid = conn.execute(
        "INSERT INTO asistencia_dia(trabajador_en_reporte_id,fecha,codigo_reportado_raw,estado_captura,estado_asistencia_id) SELECT %s,'2026-07-01','I','REGISTRADO',estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo='I' RETURNING asistencia_dia_id",
        (tid,),
    ).fetchone()["asistencia_dia_id"]
    conn.commit()
    assert len(alertas(conn)) == 1
    for _ in range(3):
        conn.execute(
            "UPDATE asistencia_dia SET hecho_asistencia_dia_id=NULL WHERE asistencia_dia_id=%s",
            (aid,),
        )
        conn.commit()
    assert len(alertas(conn)) == 1
    assert conn.execute(
        "SELECT es_disputado FROM hecho_asistencia_dia WHERE fecha='2026-07-01'"
    ).fetchone()["es_disputado"]
