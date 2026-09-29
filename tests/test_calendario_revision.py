"""RF-F15: confirmación conjunta con fuentes ficticias, solo base de pruebas."""

# ruff: noqa: F811
import uuid

import pytest
from psycopg.types.json import Jsonb

from asistia import categorias as cat
from asistia.web.calendario_anual import estados_directorio
from asistia.web.calendario_revision import propuesta
from asistia.web.lecturas import huella_reporte
from asistia.web.leyendas import huella_calendario

from .test_web import app, ciclo, client, post  # noqa: F401


def preparar_confirmacion(conn, cvid):
    conn.execute("SET TIME ZONE 'America/Lima'")
    uid = conn.execute("SELECT usuario_id FROM usuario LIMIT 1").fetchone()[
        "usuario_id"
    ]
    cats = {}
    for codigo, nombre, grupo, pago in [
        ("L", "Lectivo", "LECTIVO", "SI"),
        ("G", "Gestión", "GESTION", "SI"),
        ("D", "Sábados, domingos y feriados", "NO_LECTIVO_NI_GESTION", "NO"),
    ]:
        cats[codigo] = {
            "tipo_dia": nombre,
            "grupo_actividad": grupo,
            "es_remunerado": None,
            "fuente": "LEYENDA_DOCUMENTAL",
        }
        existente = next(
            (r for r in cat.catalogo(conn, "calendario") if r["nombre"] == nombre), None
        )
        cid = cat.guardar_categoria(
            conn,
            {
                "dominio": "calendario",
                "nombre": nombre,
                "grupo_actividad": grupo,
                "remuneracion": pago,
                "motivo": "Regla ficticia de prueba",
                "version": str(existente["version"]) if existente else "",
            },
            uid,
            uuid.uuid4(),
            existente["categoria_id"] if existente else None,
        )
        anterior = next(
            (
                e["categoria_id"]
                for e in cat.equivalencias(conn, "calendario")
                if e["significado"] == cat.normal(nombre)
            ),
            "",
        )
        cat.asociar(
            conn,
            "calendario",
            nombre,
            cid,
            anterior,
            "Equivalencia ficticia de prueba",
            uid,
            uuid.uuid4(),
        )
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos=%s WHERE calendarizacion_version_id=%s",
        (Jsonb(cats), cvid),
    )
    conn.execute(
        "DELETE FROM dia_calendarizacion WHERE calendarizacion_version_id=%s", (cvid,)
    )
    conn.execute(
        """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,codigo_reportado_raw,estado_captura,tipo_dia_id,hoja_origen,celda_origen,confianza)
        SELECT %s,d::date,CASE WHEN extract(month FROM d)<3 THEN NULL ELSE 'L' END,
        CASE WHEN extract(month FROM d)<3 THEN 'VACIO' ELSE 'REGISTRADO' END,
        CASE WHEN extract(month FROM d)<3 THEN NULL ELSE (SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno='LECTIVO') END,
        'Sheet','B13',0.85 FROM generate_series('2026-01-01'::date,'2026-12-31'::date,'1 day') d""",
        (cvid,),
    )
    conn.commit()


@pytest.fixture
def calendario(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    cv = conn.execute(
        "SELECT cv.*,cl.institucion_educativa_id FROM calendarizacion_version cv JOIN calendarizacion_local cl USING(calendarizacion_local_id)"
    ).fetchone()
    preparar_confirmacion(conn, cv["calendarizacion_version_id"])
    return rid, cv["calendarizacion_version_id"], cv["institucion_educativa_id"]


def enviar(client, conn, cvid, **extra):
    return post(
        client,
        f"/calendarios/{cvid}/confirmar",
        {"huella": propuesta(conn, cvid)["huella"], **extra},
    )


def test_confirmar_anual_reglas_historial_reenvio_y_okey(client, conn, calendario):
    rid, cvid, iid = calendario
    h = huella_calendario(conn, cvid)
    rh = huella_reporte(conn, rid)
    plan = propuesta(conn, cvid)
    assert plan["listo"] and plan["cambios"]
    assert [r["pago"] for r in plan["reglas"]] == [False, True, True]
    op = str(uuid.uuid4())
    r = enviar(
        client, conn, cvid, operacion=op, destino="reporte", reporte_id=str(rid), mes=7
    )
    assert (
        r.status_code == 303
        and r.location == f"/reportes/{rid}#report-calendar-heading"
    ), r.text
    nuevo = conn.execute(
        "SELECT * FROM calendarizacion_version WHERE version=2"
    ).fetchone()
    nid = nuevo["calendarizacion_version_id"]
    assert (
        nuevo["estado"] == "VIGENTE" and nuevo["aprobado_por"] and nuevo["aprobado_en"]
    )
    assert nuevo["version_padre_id"] == cvid and nuevo["creado_por"]
    assert huella_calendario(conn, cvid) == h and huella_reporte(conn, rid) == rh
    assert estados_directorio(conn)[iid]["estado"] == "Okey"
    assert (
        conn.execute(
            "SELECT count(*) n FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND estado_captura='VACIO'",
            (nid,),
        ).fetchone()["n"]
        == 59
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND confianza=0.85",
            (nid,),
        ).fetchone()["n"]
        == 365
    )
    html = client.get(f"/reportes/{rid}").text
    assert "data-report-calendar-status>Estado: Vigente</span>" in html
    assert "Calendarización anual confirmada y vigente" not in html
    assert "Consultar la versión anterior a esta confirmación" not in html
    again = post(
        client,
        f"/calendarios/{cvid}/confirmar",
        {"operacion": op, "huella": plan["huella"]},
    )
    assert again.status_code == 303 and str(nid) in again.location
    assert (
        enviar(client, conn, cvid, operacion=op).status_code == 303
    )  # El padre se conserva.
    assert (
        post(
            client,
            f"/calendarios/{cvid}/confirmar",
            {"operacion": op, "huella": "otra"},
        ).status_code
        == 409
    )
    assert (
        conn.execute("SELECT count(*) n FROM calendarizacion_version").fetchone()["n"]
        == 2
    )


@pytest.mark.parametrize(
    "fallo", ["vacio", "sin_fila", "remuneracion", "excepcion", "derivado", "posterior"]
)
def test_confirmar_no_oculta_pendientes_ni_sustituye_excepciones(
    client, conn, calendario, fallo
):
    _, cvid, _ = calendario
    if fallo == "vacio":
        conn.execute(
            "UPDATE dia_calendarizacion SET estado_captura='SIN_ASIGNAR',codigo_reportado_raw=NULL,tipo_dia_id=NULL WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
            (cvid,),
        )
    elif fallo == "sin_fila":
        conn.execute(
            "DELETE FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
            (cvid,),
        )
    elif fallo == "remuneracion":
        conn.execute("DELETE FROM leyenda_equivalencia")
    elif fallo == "excepcion":
        conn.execute(
            "UPDATE dia_calendarizacion SET evidencia_interpretacion=%s WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
            (
                Jsonb(
                    {
                        "clasificacion_aceptada": {
                            "tipo_dia": "Propia",
                            "grupo_actividad": "LECTIVO",
                            "es_remunerado": None,
                        }
                    }
                ),
                cvid,
            ),
        )
    elif fallo == "derivado":
        conn.execute(
            "UPDATE calendarizacion_version SET procedencia_extraccion=%s WHERE calendarizacion_version_id=%s",
            (Jsonb({"tipo": "DERIVADO_2025"}), cvid),
        )
    else:
        conn.execute(
            "INSERT INTO calendarizacion_version(calendarizacion_local_id,version,origen,motivo_version) SELECT calendarizacion_local_id,version+1,'CORRECCION_UGEL','Prueba posterior' FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
            (cvid,),
        )
    conn.commit()
    h = huella_calendario(conn, cvid)
    assert not propuesta(conn, cvid)["listo"]
    r = enviar(client, conn, cvid)
    assert r.status_code == 409 and huella_calendario(conn, cvid) == h


def test_huella_catalogo_csrf_y_fuente_sin_escritura(client, conn, calendario):
    rid, cvid, iid = calendario
    plan = propuesta(conn, cvid)
    h = huella_calendario(conn, cvid)
    for url in [
        f"/reportes/{rid}",
        f"/instituciones/{iid}?anio_calendario=2026",
        f"/calendarios/{cvid}",
    ]:
        r = client.get(url)
        assert r.status_code == 200, r.text
        assert (
            "Previsualizar original del calendario" in r.text
            and "Confirmar todo y dejar vigente" in r.text
        )
    source = client.get(f"/calendarios/{cvid}/fuente")
    assert (
        source.status_code == 200
        and 'data-rotate="90"' in source.text
        and "blob:" in source.headers["Content-Security-Policy"]
    )
    manifest = client.get(f"/calendarios/{cvid}/fuente/vista")
    assert manifest.status_code == 200, manifest.text
    page = client.get(manifest.json["imagen_url"])
    assert page.status_code == 200 and page.mimetype == "image/png"
    assert client.get(f"/calendarios/{cvid}/fuente/pagina/999").status_code == 404
    assert (
        client.get(f"/calendarios/{cvid}/fuente/vista?hoja=noexiste").status_code == 404
    )
    assert huella_calendario(conn, cvid) == h
    assert (
        client.post(
            f"/calendarios/{cvid}/confirmar", data={"huella": plan["huella"]}
        ).status_code
        == 400
    )
    conn.execute("DELETE FROM leyenda_equivalencia")
    conn.commit()
    r = post(client, f"/calendarios/{cvid}/confirmar", {"huella": plan["huella"]})
    assert r.status_code == 409 and "reglas cambiaron" in r.text
    assert huella_calendario(conn, cvid) == h


def test_confirmacion_preserva_excepcion_no_aplica_y_vigente_anterior(
    client, conn, calendario
):
    _, cvid, iid = calendario
    propia = {
        "tipo_dia": "Día excepcional revisado",
        "grupo_actividad": "GESTION",
        "es_remunerado": False,
        "fuente": "REVISION_WEB",
    }
    conn.execute(
        "UPDATE dia_calendarizacion SET evidencia_interpretacion=%s,tipo_dia_id=(SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno='GESTION') WHERE calendarizacion_version_id=%s AND fecha='2026-07-01'",
        (Jsonb({"clasificacion_aceptada": propia}), cvid),
    )
    conn.execute(
        "UPDATE dia_calendarizacion SET estado_captura='NO_APLICA',tipo_dia_id=NULL,codigo_reportado_raw=NULL WHERE calendarizacion_version_id=%s AND fecha='2026-07-02'",
        (cvid,),
    )
    conn.execute(
        "UPDATE calendarizacion_version SET estado='VIGENTE',aprobado_por=(SELECT usuario_id FROM usuario LIMIT 1),aprobado_en=now() WHERE calendarizacion_version_id=%s",
        (cvid,),
    )
    conn.commit()
    r = enviar(client, conn, cvid, destino="institucion", mes=7)
    assert r.status_code == 303, r.text
    assert (
        r.location
        == f"/instituciones/{iid}?anio_calendario=2026&mes=7#calendario-institucion-titulo"
    )
    assert (
        conn.execute(
            "SELECT estado FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
            (cvid,),
        ).fetchone()["estado"]
        == "HISTORICA"
    )
    nuevo = conn.execute(
        "SELECT calendarizacion_version_id FROM calendarizacion_version WHERE estado='VIGENTE'"
    ).fetchone()["calendarizacion_version_id"]
    dias = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND fecha IN ('2026-07-01','2026-07-02') ORDER BY fecha",
        (nuevo,),
    ).fetchall()
    assert dias[0]["evidencia_interpretacion"]["clasificacion_aceptada"] == propia
    assert dias[1]["estado_captura"] == "NO_APLICA"
    assert (
        propuesta(conn, nuevo)["confirmado"]
        and estados_directorio(conn)[iid]["estado"] == "Okey"
    )
