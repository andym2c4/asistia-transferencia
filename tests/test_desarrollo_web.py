"""RF-F31: pantallas auxiliares locales y continuidad del recorrido de RRHH."""

import io
import re

import openpyxl
import pytest

from asistia.web import create_app

from .test_revision_cargas import encolar
from .test_web import app, cargar, ciclo, client, fuentes, post  # noqa: F401

# ruff: noqa: F811

VISTAS = [
    "/documentos",
    "/cobertura",
    "/cierre",
    "/calendarios",
    "/resultados",
    "/monitoreo",
    "/monitoreo/leyendas",
    "/experimental",
]
EXPORTACIONES = [
    "/cobertura.csv",
    "/cierre/instituciones.csv",
    "/resultados/registro.csv",
    "/resultados/evidencia.json",
]


def enlaces(html):
    return re.findall(r'href="([^"?#]+)', html)


@pytest.mark.parametrize("desarrollo", [False, True])
def test_accesos_menu_hub_y_exportaciones_segun_entorno(app, client, desarrollo):
    app.config["DEVELOPMENT_TOOLS"] = desarrollo
    # La descarga completa no habilita las pantallas auxiliares.
    app.config["DEVELOPMENT_EXPORTS"] = True
    home = client.get("/")
    assert home.status_code == 200
    sidebar = home.text.split('<aside class="sidebar">')[1].split("</aside>")[0]
    assert not set(VISTAS).intersection(enlaces(sidebar))
    assert ("/desarrollo" in enlaces(sidebar)) == desarrollo
    assert "/revision" in enlaces(home.text)
    hub = client.get(
        "/desarrollo?desarrollo=1", headers={"X-Forwarded-Host": "localhost"}
    )
    assert hub.status_code == (200 if desarrollo else 404)
    if desarrollo:
        assert set(VISTAS).issubset(enlaces(hub.text))
    for ruta in VISTAS + EXPORTACIONES:
        r = client.get(ruta)
        assert r.status_code == (200 if desarrollo else 404), ruta
        assert client.head(ruta).status_code == r.status_code
        if not desarrollo:
            assert "Content-Disposition" not in r.headers
            assert not set(VISTAS).intersection(enlaces(r.text))


def test_acciones_auxiliares_no_escriben_en_produccion(app, client, conn):
    app.config["DEVELOPMENT_TOOLS"] = False
    for ruta in (
        "/documentos",
        "/cierre/preparar-salida",
        "/calendarios/codigos",
        "/trabajo",
    ):
        r = post(client, ruta, {"accion": "INICIAR", "tipo": "asistencia"})
        assert r.status_code == 404, ruta
    for tabla in ("web_recepcion", "web_salida", "web_tarea"):
        assert conn.execute(f"SELECT count(*) n FROM {tabla}").fetchone()["n"] == 0


def test_desarrollo_requiere_sesion_y_predetermina_desactivado(app, monkeypatch):
    assert app.test_client().get("/desarrollo").status_code == 302
    monkeypatch.setattr("asistia.web.entorno_local", dict)
    config = dict(app.config)
    config.pop("DEVELOPMENT_TOOLS")
    assert create_app(config).config["DEVELOPMENT_TOOLS"] is False


def test_carga_contextual_y_calendario_operativos_sin_herramientas_dev(
    app, client, conn, tmp_path
):
    rid, (_, _, archivo) = ciclo(client, conn, tmp_path)
    iid = conn.execute(
        "SELECT institucion_educativa_id FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
        (rid,),
    ).fetchone()["institucion_educativa_id"]
    cv = conn.execute(
        "SELECT calendarizacion_version_id FROM calendarizacion_version"
    ).fetchone()["calendarizacion_version_id"]
    app.config["DEVELOPMENT_TOOLS"] = False
    for ruta in (
        "/",
        "/revision",
        "/consolidado",
        "/instituciones",
        "/categorias",
        f"/instituciones/{iid}",
        f"/calendarios/{cv}",
        f"/reportes/{rid}",
    ):
        r = client.get(ruta)
        assert r.status_code == 200, ruta
        assert not set(VISTAS).intersection(enlaces(r.text)), ruta
    ruta = f"/revision/subir?institucion_id={iid}&periodo=2026-07&nivel=PRIMARIA"
    formulario = client.get(ruta)
    assert formulario.status_code == 200
    assert 'action="/revision/cargar"' in formulario.text
    assert 'name="institucion_id"' in formulario.text
    assert 'name="fecha_corte"' not in formulario.text
    assert client.get("/revision/subir").status_code == 400
    for _ in range(2):
        r = post(
            client,
            "/revision/cargar",
            {
                "tipo": "asistencia",
                "institucion_id": str(iid),
                "periodo": "2026-07",
                "nivel": "PRIMARIA",
                "archivos": (io.BytesIO(archivo.read_bytes()), archivo.name),
            },
        )
        assert r.status_code == 303
        assert f"institucion_id={iid}" in r.location
        detalle = client.get(r.location)
        assert detalle.status_code == 200
        assert (
            "El archivo contiene un reporte para la institución y el mes seleccionados"
            in detalle.text
        )
        assert "/documentos" not in enlaces(detalle.text)
    assert (
        conn.execute("SELECT count(*) n FROM reporte_asistencia").fetchone()["n"] == 1
    )


def test_lectura_asistida_permanece_disponible_sin_cierre_general(
    app, client, conn, tmp_path
):
    nx, _, _ = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    archivo = tmp_path / "reporte_sin_tabla.xlsx"
    libro = openpyxl.Workbook()
    libro.active["A1"] = "Reporte ficticio sin estructura conocida"
    libro.save(archivo)
    libro.close()
    app.config["DEVELOPMENT_TOOLS"] = False
    r = encolar(client, archivo)
    assert r.status_code == 200 and r.json["estado"] == "PARCIAL"
    detalle = client.get(r.json["continuar_url"])
    assert detalle.status_code == 200 and "Reintentar extracción" in detalle.text
    assert "/cierre" not in enlaces(detalle.text)
    assert "/revision" in enlaces(detalle.text)
    assert (
        client.get(r.json["continuar_url"] + "/original").data == archivo.read_bytes()
    )
    assert client.get("/cierre").status_code == 404
