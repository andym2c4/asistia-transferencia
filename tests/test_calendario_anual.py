"""Año completo compacto: sin imputar categorías, con totales conciliables."""

# ruff: noqa: F811
from datetime import date

from psycopg.types.json import Jsonb

from asistia.web.calendario_anual import calendario_anual, estados_directorio

from .test_institucion_detalle import calendario
from .test_instituciones import institucion
from .test_web import app, client  # noqa: F401


def test_anual_bisiesto_semanas_y_leyenda_pendiente_sin_imputar():
    cv = {
        "anio": 2024,
        "clasificacion_codigos": {
            "L": {
                "tipo_dia": "Lectivo",
                "grupo_actividad": "LECTIVO",
                "es_remunerado": None,
            }
        },
    }
    dias = [
        {
            "fecha": date(2024, 2, 29),
            "estado_captura": "REGISTRADO",
            "codigo_reportado_raw": "L",
            "grupo_recibido": "LECTIVO",
        },
        {"fecha": date(2024, 3, 1), "estado_captura": "NO_APLICA"},
    ]
    d = calendario_anual(cv, dias)
    assert len(d["meses"]) == 12 and all(len(m["celdas"]) == 42 for m in d["meses"])
    assert sum(d["totales"].values()) == 366
    assert (
        d["totales"]["LECTIVO"] == 1
        and d["totales"]["NO_APLICA"] == 1
        and d["totales"][""] == 364
    )
    # El 29/2/2024 es jueves y conserva actividad conocida con pago pendiente.
    celda = next(c for c in d["meses"][1]["celdas"] if c and c["fecha"].day == 29)
    assert d["meses"][1]["celdas"].index(celda) % 7 == 3
    assert celda["simbolo"] == "L" and not celda["aviso"]
    assert d["leyenda"][0]["avisos"] == ["Falta confirmar la remuneración"]


def test_fuente_desconocida_conflicto_y_excepcion_diaria_se_conservan():
    c = {"tipo_dia": "Lectivo", "grupo_actividad": "LECTIVO", "es_remunerado": True}
    cv = {
        "anio": 2026,
        "clasificacion_codigos": {
            "L": c,
            "X": dict(c, conflictos=["categorías contradictorias"]),
            "Texto de categoría largo": c,
        },
    }
    dias = [
        {
            "fecha": date(2026, 3, 1),
            "estado_captura": "REGISTRADO",
            "codigo_reportado_raw": "X",
            "grupo_recibido": "LECTIVO",
        },
        {
            "fecha": date(2026, 3, 2),
            "estado_captura": "REGISTRADO",
            "codigo_reportado_raw": "L",
            "grupo_recibido": "GESTION",
            "evidencia_interpretacion": {
                "clasificacion_aceptada": dict(
                    c, tipo_dia="Gestión especial", grupo_actividad="GESTION"
                )
            },
        },
        {
            "fecha": date(2026, 3, 3),
            "estado_captura": "ILEGIBLE",
            "codigo_reportado_raw": None,
        },
        {
            "fecha": date(2026, 3, 4),
            "estado_captura": "REGISTRADO",
            "codigo_reportado_raw": "Z",
        },
    ]
    d = calendario_anual(cv, dias)
    celdas = [c for c in d["meses"][2]["celdas"] if c]
    assert celdas[0]["grupo"] == ""
    assert (
        celdas[1]["grupo"] == "GESTION"
        and celdas[1]["aviso"]
        and "Gestión especial" in celdas[1]["detalle"]
    )
    assert celdas[2]["simbolo"] == "!" and "ilegible" in celdas[2]["detalle"]
    assert next(c for c in d["leyenda"] if c["codigo"] == "Z")["avisos"]
    assert (
        len(
            next(c for c in d["leyenda"] if c["codigo"] == "Texto de categoría largo")[
                "simbolo"
            ]
        )
        <= 3
    )
    assert sum(d["totales"].values()) == 365


def test_estados_pendiente_revision_okey_y_borrador_posterior(client, conn):
    sin = institucion(conn)
    revisar = institucion(conn, "9999002")
    calendario(conn, revisar)
    completo = institucion(conn, "9999003")
    cvid = calendario(conn, completo, estado="VIGENTE")
    # Fechas y categoría completas para el año, sin confundir una sola fila con cobertura anual.
    conn.execute(
        """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,codigo_reportado_raw,estado_captura)
      SELECT %s,d::date,(SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno='LECTIVO'),'L','REGISTRADO'
      FROM generate_series('2026-01-01'::date,'2026-12-31'::date,'1 day') d
      ON CONFLICT(calendarizacion_version_id,fecha) DO NOTHING""",
        (cvid,),
    )
    estados = estados_directorio(conn)
    assert sin not in estados
    assert (
        estados[revisar]["estado"] == "Por revisar"
        and estados[completo]["estado"] == "Okey"
    )
    conn.commit()
    html = client.get("/instituciones").text
    assert all(
        label in html
        for label in ["Calendarización", "Pendiente", "Por revisar", "Okey"]
    )
    calendario(conn, completo, version=2)
    assert estados_directorio(conn)[completo]["estado"] == "Por revisar"


def test_vigente_con_leyenda_pendiente_no_se_presenta_okey(client, conn):
    iid = institucion(conn)
    cvid = calendario(conn, iid, estado="VIGENTE")
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos=%s WHERE calendarizacion_version_id=%s",
        (
            Jsonb(
                {
                    "L": {
                        "tipo_dia": "Lectivo",
                        "grupo_actividad": "LECTIVO",
                        "es_remunerado": None,
                    }
                }
            ),
            cvid,
        ),
    )
    assert estados_directorio(conn)[iid]["estado"] == "Por revisar"
    conn.commit()
    html = client.get(f"/instituciones/{iid}").text
    assert (
        html.count("data-annual-month=") == 12
        and html.count('class="annual-warning"') == 1
    )
    assert "data-day-input" not in html and "Pendiente de clasificar" not in html
    assert "annual-tooltip" in html and "Leyenda de esta institución" in html
