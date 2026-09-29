"""RF-F13: consulta del mes reportado sin sustituir institución, año ni versión."""

# ruff: noqa: F811
from datetime import date

import pytest
from psycopg.types.json import Jsonb

from asistia.web.lecturas import huella_reporte
from asistia.web.reporte_calendario import calendario_reporte

from .test_institucion_detalle import calendario
from .test_instituciones import institucion
from .test_web import app, ciclo, client  # noqa: F401


def test_mes_propio_vigente_con_borrador_mas_nuevo(client, conn):
    iid = institucion(conn)
    calendario(conn, institucion(conn, "9999002"), estado="VIGENTE")
    calendario(conn, iid, anio=2027, estado="VIGENTE")
    vigente = calendario(conn, iid, estado="VIGENTE")
    borrador = calendario(conn, iid, version=2)
    fechas = [date(2026, 7, d) for d in range(1, 32)]
    vista = calendario_reporte(
        conn, {"institucion_educativa_id": iid, "periodo": date(2026, 7, 1)}, fechas
    )
    assert vista["version"]["calendarizacion_version_id"] == vigente
    assert vista["ultima"]["calendarizacion_version_id"] == borrador
    assert [d["fecha"] for d in vista["dias"]] == fechas
    assert vista["alineado"]
    assert vista["dias"][0]["grupo"] == "LECTIVO"
    # Un día sin fuente sigue desconocido, sin inferir falta ni actividad.
    assert vista["dias"][1]["simbolo"] == "·"
    assert vista["dias"][1]["grupo"] == ""


@pytest.mark.parametrize("estado", ["BORRADOR", "HISTORICA", "RECHAZADA"])
def test_no_sustituir_anio_faltante_ni_version_cerrada(client, conn, estado):
    iid = institucion(conn)
    calendario(conn, iid, anio=2025, estado="VIGENTE")
    calendario(conn, iid, estado=estado)
    assert (
        calendario_reporte(
            conn, {"institucion_educativa_id": None, "periodo": date(2026, 7, 1)}, []
        )
        is None
    )
    vista = calendario_reporte(
        conn, {"institucion_educativa_id": iid, "periodo": date(2026, 7, 1)}, []
    )
    assert bool(vista) == (estado == "BORRADOR")
    assert (
        calendario_reporte(
            conn, {"institucion_educativa_id": iid, "periodo": date(2024, 7, 1)}, []
        )
        is None
    )


def test_febrero_bisiesto_sin_rellenar_actividad(client, conn):
    iid = institucion(conn)
    calendario(conn, iid, anio=2024)
    vista = calendario_reporte(
        conn,
        {"institucion_educativa_id": iid, "periodo": date(2024, 2, 1)},
        [date(2024, 2, 1)],
    )
    assert len(vista["dias"]) == 29 and not vista["alineado"]
    assert all(d["simbolo"] == "·" and d["grupo"] == "" for d in vista["dias"])


def test_render_reportado_consulta_sin_escrituras(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    cvid = conn.execute(
        "SELECT calendarizacion_version_id FROM calendarizacion_version"
    ).fetchone()["calendarizacion_version_id"]
    conn.execute(
        "UPDATE calendarizacion_version SET procedencia_extraccion=%s WHERE calendarizacion_version_id=%s",
        (Jsonb({"tipo": "DERIVADO_2025"}), cvid),
    )
    conn.commit()
    antes = huella_reporte(conn, rid)
    calendarios = conn.execute(
        "SELECT to_jsonb(v) datos FROM calendarizacion_version v ORDER BY calendarizacion_version_id"
    ).fetchall()
    response = client.get(f"/reportes/{rid}")
    assert response.status_code == 200
    html = response.text
    assert (
        html.index("data-original-preview")
        < html.index('data-report-pane="digital"')
        < html.index('id="observaciones"')
        < html.index('id="coherencia"')
        < html.index('data-coherence-adjustment')
        < html.index('data-coherence-workspace')
        < html.index('id="report-calendar-heading"')
    )
    assert '<details class="report-original" data-original-preview>' in html
    assert html.count('data-calendar-date="2026-07-') == 31
    assert 'data-calendar-date="2026-08-' not in html
    assert html.count('id="report-calendar-heading"') == 1
    assert 'data-aligned="false"' in html
    assert 'class="report-month-calendar"' in html
    assert 'data-calendar-date="2026-07-01" data-weekday="3"' in html
    assert "Pendiente de aprobación" in html and "Basado en información de 2025" in html
    assert "data-calendar-save" not in html and "data-day-input" not in html
    assert huella_reporte(conn, rid) == antes
    assert (
        calendarios
        == conn.execute(
            "SELECT to_jsonb(v) datos FROM calendarizacion_version v ORDER BY calendarizacion_version_id"
        ).fetchall()
    )
    # La ausencia del año exacto se explica, sin mostrar otro calendario.
    conn.execute("UPDATE calendarizacion_local SET anio=2025")
    conn.commit()
    html = client.get(f"/reportes/{rid}").text
    assert "No hay un calendario disponible para esta institución en 2026." in html
    assert "data-calendar-date=" not in html
