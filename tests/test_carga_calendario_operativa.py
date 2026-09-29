"""RF-F36: cargar calendarios forma parte del recorrido de producción."""

# ruff: noqa: F811
import io
import uuid

from .test_web import app, cargar, client, fuentes, post  # noqa: F401


def preparar(app, client, conn, tmp_path):
    nx, cal, _ = fuentes(tmp_path)
    assert cargar(client, nx, "nexus").status_code == 303
    iid = conn.execute(
        "SELECT institucion_educativa_id FROM institucion_educativa"
    ).fetchone()["institucion_educativa_id"]
    app.config["DEVELOPMENT_TOOLS"] = False
    return iid, cal


def enviar(client, iid, cal, **extra):
    return post(
        client,
        f"/instituciones/{iid}/calendario/subir",
        {
            "tipo": "calendario",
            "periodo": "2026-07",
            "nivel": "PRIMARIA",
            "archivos": (io.BytesIO(cal.read_bytes()), cal.name),
            **extra,
        },
    )


def test_carga_primera_y_repetida_en_produccion(app, client, conn, tmp_path):
    iid, cal = preparar(app, client, conn, tmp_path)
    ruta = f"/instituciones/{iid}/calendario/subir"
    assert ruta in client.get(f"/instituciones/{iid}").text
    assert "Cargar calendario" in client.get(ruta).text
    assert client.get("/documentos").status_code == 404
    op = str(uuid.uuid4())
    first = enviar(client, iid, cal, operacion=op)
    assert first.status_code == 303
    result = client.get(first.location)
    assert result.status_code == 200
    assert "Revisar calendario extraído" in result.text
    assert "Aún falta identificar el reporte" not in result.text
    again = enviar(client, iid, cal, operacion=op)
    assert again.location == first.location
    assert (
        conn.execute("SELECT count(*) n FROM calendarizacion_version").fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_recepcion r JOIN web_carga c USING(web_carga_id) WHERE c.tipo='calendario'"
        ).fetchone()["n"]
        == 1
    )


def test_no_reasigna_calendario_de_otra_institucion(app, client, conn, tmp_path):
    iid, cal = preparar(app, client, conn, tmp_path)
    otro = conn.execute(
        "INSERT INTO institucion_educativa(local_educativo_id,cod_mod,anexo,nombre_ie,nivel_modalidad,distrito) SELECT local_educativo_id,'9999002','0','Otra IE ficticia',nivel_modalidad,distrito FROM institucion_educativa LIMIT 1 RETURNING institucion_educativa_id"
    ).fetchone()["institucion_educativa_id"]
    conn.commit()
    r = enviar(client, otro, cal)
    assert r.status_code == 303
    assert "Corresponde a otra institución" in client.get(r.location).text
    assert (
        conn.execute(
            "SELECT institucion_educativa_id FROM calendarizacion_local"
        ).fetchone()["institucion_educativa_id"]
        == iid
    )


def test_restricciones_sesion_tipo_y_archivo(app, client, conn, tmp_path):
    iid, cal = preparar(app, client, conn, tmp_path)
    ruta = f"/instituciones/{iid}/calendario/subir"
    assert app.test_client().get(ruta).status_code == 302
    assert client.post(ruta, data={"tipo": "calendario"}).status_code == 400
    assert enviar(client, iid, cal, tipo="nexus").status_code == 400
    assert enviar(client, 9999999, cal).status_code == 404
    assert post(client, ruta, {"tipo": "calendario"}).status_code == 400
    assert (
        conn.execute("SELECT count(*) n FROM calendarizacion_version").fetchone()["n"]
        == 0
    )
