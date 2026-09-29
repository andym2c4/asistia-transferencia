"""RF-F04: filtros cruzados coherentes con personas y retorno; solo base aislada."""
# ruff: noqa: F811

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from asistia.web.instituciones import directorio

from .test_institucion_detalle import reporte
from .test_instituciones import confirmar, institucion, vinculo
from .test_web import app, client  # noqa: F401


def incluir(*valores):
    return {"modo": "incluir", "valores": list(valores)}


def cuentas(datos, columna):
    return {
        v["valor"]: v["cantidad"]
        for f in datos["facetas"]
        if f["columna"] == columna
        for v in f["valores"]
    }


def test_filtros_cruzados_facetas_y_movimientos_comparten_poblacion(conn):
    a = institucion(conn)
    b = institucion(conn, "9999002")
    c = institucion(conn, "9999003", "Secundaria")
    conn.execute(
        "UPDATE institucion_educativa SET distrito='OTRO',centro_poblado=NULL WHERE institucion_educativa_id=%s",
        (b,),
    )
    for iid in (a, b, c):
        par = vinculo(conn, iid)
        confirmar(conn, par)
        vinculo(conn, iid, par[0])  # dos vínculos siguen siendo una persona
    filtro = {"distrito": incluir("LUYA", "OTRO"), "centro_poblado": incluir("")}
    d = directorio(conn, columnas=filtro)
    assert d["total"] == d["nuevos"] == 1
    assert d["instituciones"][0]["institucion_educativa_id"] == b
    assert cuentas(d, "distrito") == {"LUYA": 0, "OTRO": 1}
    assert cuentas(d, "centro_poblado") == {"": 1, "Centro ficticio": 2}
    assert cuentas(d, "personas") == {"1": 1}
    filtro["centro_poblado"] = incluir("", "Centro ficticio")
    d = directorio(conn, nivel_ie="PRIMARIA", columnas=filtro)
    assert d["total"] == d["nuevos"] == 2
    assert sum(cuentas(d, "distrito").values()) == 2
    assert directorio(conn, q="9999003", columnas=filtro)["nuevos"] == 1


def test_excluir_y_seleccion_vacia_se_pueden_recuperar(conn):
    institucion(conn)
    institucion(conn, "9999002")
    d = directorio(
        conn, columnas={"cod_mod": {"modo": "excluir", "valores": ["9999001"]}}
    )
    assert [i["cod_mod"] for i in d["instituciones"]] == ["9999002"]
    assert cuentas(d, "cod_mod") == {"9999001": 1, "9999002": 1}
    d = directorio(conn, columnas={"cod_mod": incluir()})
    assert d["total"] == 0
    assert sum(cuentas(d, "cod_mod").values()) == 2


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        '{"dni":{}}',
        '{"distrito":[]}',
        '{"distrito":{"modo":"incluir","valores":[1]}}',
        '{"distrito":{"modo":"otro","valores":[]}}',
    ],
)
def test_filtros_invalidos_responden_400(client, raw):
    assert client.get("/instituciones", query_string={"fc": raw}).status_code == 400


def test_filtros_via_http_se_conservan_en_detalle_y_vacio(client, conn):
    institucion(conn)
    institucion(conn, "9999002")
    conn.commit()
    filtro = json.dumps({"cod_mod": incluir("9999002")})
    html = client.get(
        "/instituciones", query_string={"fc": filtro, "q": "ficticia"}
    ).text
    assert html.count("data-institution-row") == 1
    link = unescape(re.search(r'<a data-institution-link href="([^"]+)"', html)[1])
    assert json.loads(parse_qs(urlsplit(link).query)["fc"][0]) == json.loads(filtro)
    detalle = client.get(link).text
    retorno = unescape(re.search(r'<a href="([^"]+)#directorio-titulo">', detalle)[1])
    assert client.get(retorno).text.count("data-institution-row") == 1
    html = client.get(
        "/instituciones", query_string={"fc": json.dumps({"cod_mod": incluir()})}
    ).text
    assert "No hay coincidencias" in html and html.count("data-filter=") == 6
    assert "Limpiar filtros" in html


def test_reportes_etiquetan_meses_y_conservan_fecha_para_ordenar(client, conn):
    iid = institucion(conn)
    reporte(conn, iid, "2025-12-01")
    for mes in range(1, 13):
        reporte(conn, iid, f"2026-{mes:02}-01")
    conn.commit()
    html = client.get(f"/instituciones/{iid}").text
    assert "07-JULIO</a><small>2026</small>" in html
    assert "12-DICIEMBRE</a><small>2025</small>" in html
    fechas = re.findall(r'data-periodo="([^"]+)"', html)
    assert len(fechas) == 13 and fechas == sorted(fechas, reverse=True)
    assert "data-report-sort" in html
