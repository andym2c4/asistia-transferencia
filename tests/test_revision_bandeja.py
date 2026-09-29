"""RF-F26: instituciones/mes, versiones y carga contextual en PostgreSQL aislado."""

import io
from datetime import date

import pytest

from asistia.web.lecturas import huella_reporte
from asistia.web.revision_bandeja import instituciones_revision

from .test_instituciones import institucion
from .test_web import app, cargar, ciclo, client, fuentes, post  # noqa: F401

# ruff: noqa: F811

MES = date(2026, 7, 1)


def test_bandeja_incluye_sin_reporte_por_mes_nivel_y_anexo(client, conn, tmp_path):
    rid, _ = ciclo(client, conn, tmp_path)
    faltante = institucion(conn, "9999001", anexo="1")
    institucion(conn, "9999002", nivel="Secundaria")
    conn.commit()
    antes = huella_reporte(conn, rid)
    filas = instituciones_revision(conn, MES, "PRIMARIA")
    assert len(filas) == 2
    sin_reporte = next(r for r in filas if r["institucion_educativa_id"] == faltante)
    assert sin_reporte["estado"] == "Sin reporte cargado"
    assert sin_reporte["reportes"] == []
    assert sum(len(r["reportes"]) for r in filas) == 1
    assert all(
        not r["reportes"]
        for r in instituciones_revision(conn, date(2026, 8, 1), "PRIMARIA")
    )
    page = client.get("/revision?periodo=2026-07&nivel=PRIMARIA")
    assert page.status_code == 200
    assert "Subir reporte mensual" in page.text
    assert "Abrir revisión" in page.text
    assert 'action="/revision" class="scope"' in page.text
    assert huella_reporte(conn, rid) == antes


def test_varios_turnos_una_institucion_ultima_version_y_nivel_reportado(
    client, conn, tmp_path
):
    rid, _ = ciclo(client, conn, tmp_path)
    reporte = conn.execute(
        "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s", (rid,)
    ).fetchone()
    iid = reporte["institucion_educativa_id"]
    serie = conn.execute(
        "INSERT INTO reporte_asistencia_serie(institucion_educativa_id,periodo,nivel_modalidad,turno) VALUES (%s,%s,'PRIMARIA','TARDE') RETURNING reporte_asistencia_serie_id",
        (iid, MES),
    ).fetchone()["reporte_asistencia_serie_id"]
    for version in (1, 2):
        conn.execute(
            """INSERT INTO reporte_asistencia(reporte_asistencia_serie_id,institucion_educativa_id,documento_recibido_id,periodo,version,tipo_fuente,institucion_reportada_raw,indice_bloque)
            VALUES (%s,%s,%s,%s,%s,'EXCEL_NATIVO','Ficticia',2)""",
            (serie, iid, reporte["documento_recibido_id"], MES, version),
        )
    # El nivel del padrón puede cambiar; no ocultar el reporte del ámbito consultado.
    conn.execute(
        "UPDATE institucion_educativa SET nivel_modalidad='Secundaria' WHERE institucion_educativa_id=%s",
        (iid,),
    )
    conn.commit()
    filas = instituciones_revision(conn, MES, "PRIMARIA")
    assert len(filas) == 1
    assert len(filas[0]["reportes"]) == 2
    assert (
        next(r for r in filas[0]["reportes"] if r["turno"] == "TARDE")["version"] == 2
    )
    assert (
        filas[0]["filas"] == 1
    )  # una fila de persona, no multiplicada por los turnos/versiones
    assert (
        "Ver 2 reportes" in client.get("/revision?periodo=2026-07&nivel=PRIMARIA").text
    )


@pytest.mark.parametrize("estado", ["RECHAZADO", "HISTORICA"])
def test_fuentes_no_vigentes_no_cuentan_como_reporte_cargado(
    client, conn, tmp_path, estado
):
    rid, _ = ciclo(client, conn, tmp_path)
    conn.execute(
        "UPDATE reporte_asistencia SET estado=%s WHERE reporte_asistencia_id=%s",
        (estado, rid),
    )
    conn.commit()
    assert (
        instituciones_revision(conn, MES, "PRIMARIA")[0]["estado"]
        == "Sin reporte cargado"
    )


def test_carga_contextual_preserva_fuente_y_reintento_idempotente(
    client, conn, tmp_path
):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    iid = conn.execute(
        "SELECT institucion_educativa_id FROM institucion_educativa"
    ).fetchone()["institucion_educativa_id"]
    ruta = f"/documentos?institucion_id={iid}&periodo=2026-07&nivel=PRIMARIA"
    page = client.get(ruta)
    assert page.status_code == 200
    assert 'name="tipo" value="asistencia"' in page.text
    assert 'name="institucion_id"' in page.text
    assert 'name="fecha_corte"' not in page.text
    assert "Archivos recibidos" not in page.text
    assert "Volver a Revisión" in page.text
    for _ in range(2):
        response = post(
            client,
            "/documentos",
            {
                "institucion_id": str(iid),
                "tipo": "asistencia",
                "periodo": "2026-07",
                "nivel": "PRIMARIA",
                "archivos": (io.BytesIO(archivo.read_bytes()), archivo.name),
            },
        )
        assert response.status_code == 303
        assert f"institucion_id={iid}" in response.location
        resultado = client.get(response.location)
        assert resultado.status_code == 200
        assert (
            "El archivo contiene un reporte para la institución y el mes seleccionados"
            in resultado.text
        )
    assert (
        conn.execute("SELECT count(*) n FROM reporte_asistencia").fetchone()["n"] == 1
    )
    assert instituciones_revision(conn, MES, "PRIMARIA")[0]["estado"] == "Por revisar"


def test_archivo_de_otra_ie_o_mes_no_se_reasigna_al_destino(client, conn, tmp_path):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    otro = institucion(conn, "9999002")
    conn.commit()
    response = post(
        client,
        "/documentos",
        {
            "institucion_id": str(otro),
            "tipo": "asistencia",
            "periodo": "2026-08",
            "nivel": "PRIMARIA",
            "archivos": (io.BytesIO(archivo.read_bytes()), archivo.name),
        },
    )
    assert response.status_code == 303
    resultado = client.get(response.location)
    assert (
        "El archivo no contiene un reporte vigente para la institución y el mes seleccionados"
        in resultado.text
    )
    r = conn.execute(
        "SELECT institucion_educativa_id,periodo FROM reporte_asistencia"
    ).fetchone()
    assert r["institucion_educativa_id"] != otro
    assert r["periodo"] == MES
    assert all(
        not i["reportes"]
        for i in instituciones_revision(conn, date(2026, 8, 1), "PRIMARIA")
    )


@pytest.mark.parametrize("iid,status", [("no-id", 400), ("999999", 404), ("-1", 400)])
def test_contexto_invalido_no_recibe_archivos(client, conn, iid, status):
    assert client.get(f"/documentos?institucion_id={iid}").status_code == status
    response = post(
        client,
        "/documentos",
        {
            "institucion_id": iid,
            "tipo": "asistencia",
            "archivos": (io.BytesIO(b"ficticio"), "fuente.txt"),
        },
    )
    assert response.status_code == status
    assert conn.execute("SELECT count(*) n FROM web_recepcion").fetchone()["n"] == 0


@pytest.mark.parametrize("tipo,cantidad", [("calendario", 1), ("asistencia", 2)])
def test_carga_por_institucion_rechaza_otro_tipo_o_varios_archivos(
    client, conn, tipo, cantidad
):
    iid = institucion(conn)
    conn.commit()
    response = post(
        client,
        "/documentos",
        {
            "institucion_id": str(iid),
            "tipo": tipo,
            "archivos": [
                (io.BytesIO(b"ficticio"), f"fuente{n}.txt") for n in range(cantidad)
            ],
        },
    )
    assert response.status_code == 400
    assert conn.execute("SELECT count(*) n FROM web_recepcion").fetchone()["n"] == 0


def test_carga_contextual_sin_extraccion_conserva_original_y_retorno(client, conn):
    iid = institucion(conn)
    conn.commit()
    response = post(
        client,
        "/documentos",
        {
            "institucion_id": str(iid),
            "tipo": "asistencia",
            "periodo": "2026-07",
            "nivel": "PRIMARIA",
            "archivos": (io.BytesIO(b"original ficticio sin estructura"), "fuente.txt"),
        },
    )
    assert response.status_code == 303
    page = client.get(response.location)
    assert page.status_code == 200
    assert "Aún falta identificar el reporte" in page.text
    assert "Volver a Revisión" in page.text
    assert "Descargar original" in page.text
    assert conn.execute("SELECT count(*) n FROM web_recepcion").fetchone()["n"] == 1
    assert (
        instituciones_revision(conn, MES, "PRIMARIA")[0]["estado"]
        == "Sin reporte cargado"
    )
