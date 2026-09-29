"""RF-F05: operaciones de RRHH y complemento CSV, siempre sobre base aislada."""
# ruff: noqa: F811

import re
import uuid

import psycopg
import pytest

from asistia.padron_centros import analizar, completar, cotejar
from asistia.web.instituciones_edicion import huella, leer

from .test_institucion_detalle import calendario, reporte
from .test_instituciones import institucion, vinculo
from .test_web import app, client, post  # noqa: F401


def datos(**kw):
    return {
        "cod_mod": "0012345",
        "anexo": "0",
        "nombre_ie": "Institución ficticia nueva",
        "nivel_modalidad": "Primaria",
        "departamento": "AMAZONAS",
        "provincia": "LUYA",
        "distrito": "LAMUD",
        "centro_poblado": "Centro ficticio",
        "direccion": "",
        "motivo": "Registro ficticio con fuente de prueba",
        **kw,
    }


def test_creacion_idempotente_y_unicidad_identidad(client, conn):
    op = str(uuid.uuid4())
    data = datos(operacion=op)
    r = post(client, "/instituciones/nueva", data)
    assert r.status_code == 303
    iid = int(r.location.rsplit("/", 1)[1])
    assert post(client, "/instituciones/nueva", data).location == r.location
    ie = leer(conn, iid)
    assert ie["cod_mod"] == "0012345" and ie["turno"] == "" and ie["area_censal"] == ""
    assert conn.execute("SELECT count(*) n FROM local_educativo").fetchone()["n"] == 1
    assert (
        conn.execute("SELECT count(*) n FROM institucion_cambio").fetchone()["n"] == 1
    )
    assert post(client, "/instituciones/nueva", datos()).status_code == 409
    assert (
        post(client, "/instituciones/nueva", {**data, "nombre_ie": "Otra"}).status_code
        == 409
    )
    assert post(client, "/instituciones/nueva", datos(anexo="1")).status_code == 303


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("cod_mod", "123"),
        ("anexo", "x"),
        ("nombre_ie", ""),
        ("distrito", ""),
        ("nivel_modalidad", "x" * 81),
        ("motivo", ""),
    ],
)
def test_validacion_conserva_formulario_y_no_crea(client, conn, campo, valor):
    r = post(client, "/instituciones/nueva", datos(**{campo: valor}))
    assert r.status_code == 400 and "data-error" in r.text
    assert 'value="0012345"' in r.text or campo == "cod_mod"
    assert (
        conn.execute("SELECT count(*) n FROM institucion_educativa").fetchone()["n"]
        == 0
    )
    assert conn.execute("SELECT count(*) n FROM local_educativo").fetchone()["n"] == 0


def test_edicion_conserva_vinculos_reportes_calendario_y_conflicto(client, conn):
    iid = institucion(conn)
    _, vid = vinculo(conn, iid)
    rid = reporte(conn, iid, "2026-07-01")
    calendario(conn, iid)
    conn.commit()
    original = leer(conn, iid)
    data = datos(huella=huella(original), cod_mod="0000001", anexo="01")
    ruta = f"/instituciones/{iid}/editar?q=ficticia"
    r = post(client, ruta, data, headers={"X-ASISTIA-Async": "1"})
    assert r.status_code == 200 and r.json["redirect"].endswith("?q=ficticia")
    ie = leer(conn, iid)
    assert ie["local_educativo_id"] == original["local_educativo_id"]
    assert ie["gestion"] == original["gestion"] and ie["cod_mod"] == "0000001"
    assert (
        conn.execute(
            "SELECT institucion_educativa_id FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id=%s",
            (vid,),
        ).fetchone()["institucion_educativa_id"]
        == iid
    )
    assert (
        conn.execute(
            "SELECT institucion_educativa_id FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
            (rid,),
        ).fetchone()["institucion_educativa_id"]
        == iid
    )
    assert post(client, ruta, {**data, "nombre_ie": "Obsoleta"}).status_code == 409
    cambio = conn.execute("SELECT * FROM institucion_cambio").fetchone()
    assert (
        cambio["valores_anteriores"]["cod_mod"] == "9999001"
        and cambio["autor"] is not None
    )
    assert "Historial de cambios" in client.get(ruta).text
    with pytest.raises(psycopg.Error, match="append-only"), conn.transaction():
        conn.execute("DELETE FROM institucion_cambio")


def test_csrf_y_sesion_requeridas(client, conn):
    assert client.post("/instituciones/nueva", data=datos()).status_code == 400
    conn.execute("DELETE FROM web_sesion")
    conn.commit()
    assert post(client, "/instituciones/nueva", datos()).status_code == 302
    assert (
        conn.execute("SELECT count(*) n FROM institucion_educativa").fetchone()["n"]
        == 0
    )


def test_formularios_y_enlaces(client, conn):
    iid = institucion(conn)
    conn.commit()
    assert 'href="/instituciones/nueva' in client.get("/instituciones").text
    assert f"/instituciones/{iid}/editar" in client.get(f"/instituciones/{iid}").text
    assert re.search(
        'name="huella" value="[a-f0-9]{64}"',
        client.get(f"/instituciones/{iid}/editar").text,
    )


def test_padron_solo_completa_vacios_por_codigo_y_anexo_y_es_repetible(conn):
    a = institucion(conn, "0012345")
    b = institucion(conn, "0012345", anexo="1")
    institucion(conn, "0012346")
    conn.execute(
        "UPDATE institucion_educativa SET centro_poblado=NULL WHERE institucion_educativa_id=%s",
        (a,),
    )
    csv = (
        b"COD_MOD,ANEXO,CEN_POB\n0012345,0,  Centro recibido  \n0012345,1,Otro centro\n"
    )
    filas, sha = analizar(csv)
    antes, _ = cotejar(conn, filas)
    assert (
        antes["por_completar"]
        == antes["conflictos_conservados"]
        == antes["sin_correspondencia"]
        == 1
    )
    completar(conn, filas, sha, {"nombre": "Ficticio.csv"})
    completar(conn, filas, sha, {"nombre": "Ficticio.csv"})
    assert leer(conn, a)["centro_poblado"] == "Centro recibido"
    assert leer(conn, b)["centro_poblado"] == "Centro ficticio"
    audit = conn.execute("SELECT * FROM institucion_cambio").fetchall()
    assert (
        len(audit) == 1
        and audit[0]["fuente"]["valor_recibido"] == "  Centro recibido  "
    )
    assert (
        audit[0]["fuente"]["fila"] == 2
        and audit[0]["valores_anteriores"]["centro_poblado"] is None
    )
    conn.execute(
        "UPDATE institucion_educativa SET centro_poblado=NULL WHERE institucion_educativa_id=%s",
        (a,),
    )
    completar(conn, filas, sha, {})
    assert leer(conn, a)["centro_poblado"] is None  # no deshace una edición posterior


@pytest.mark.parametrize(
    "contenido",
    [
        b"COD_MOD,ANEXO,CEN_POB\n123,0,Centro\n",
        b"COD_MOD,ANEXO,CEN_POB\n0012345,0,A\n0012345,0,B\n",
        b"COD_MOD,ANEXO\n0012345,0\n",
    ],
)
def test_padron_rechaza_ambiguedad_y_columnas_faltantes(contenido):
    with pytest.raises(ValueError):
        analizar(contenido)
