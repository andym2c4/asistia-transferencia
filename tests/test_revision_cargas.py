"""RF-F27: cola por archivo, recepción durable y continuidad de revisión."""

import io
import uuid
from datetime import date

import openpyxl
import pytest

from asistia.niveles import NIVELES
from asistia.web.auth import crear_usuario
from asistia.web.db import WRITE_LOCK
from asistia.web.revision_bandeja import instituciones_revision
from asistia.web.revision_cargas import cargas_recientes

from .fixtures_excel import construir_anexo3_minimo, construir_nexus_minimo
from .test_web import app, cargar, client, fuentes, post  # noqa: F401

# ruff: noqa: F811

HEADERS = {"Accept": "application/json", "X-ASISTIA-Async": "1"}


def encolar(client, archivo, **datos):
    return post(
        client,
        "/revision/cargar",
        {
            "tipo": "asistencia",
            "periodo": "2026-09",
            "nivel": "SECUNDARIA",
            "archivos": (io.BytesIO(archivo.read_bytes()), archivo.name),
            **datos,
        },
        headers=HEADERS,
    )


@pytest.mark.parametrize("modo", ["cola", "lote_html"])
@pytest.mark.parametrize("nivel_filtro", NIVELES)
def test_carga_tres_niveles_desde_cualquier_filtro(
    client, conn, tmp_path, modo, nivel_filtro
):
    """El ámbito filtra la consulta, nunca la identidad de los archivos recibidos."""
    mes = date(2026, 9, 1)
    niveles = ("Inicial", "Primaria", "Secundaria")
    nexus = []
    archivos = []
    for numero, nivel in enumerate(niveles, 1):
        codigo = f"999910{numero}"
        dni = f"9999100{numero}"
        nombre = f"{codigo} IE FICTICIA {nivel.upper()}"
        nexus.append(
            {
                "CODMOD I.E.": codigo,
                "CODIGO DE PLAZA": f"PZ-FICTICIA-{numero}",
                "TIPO DE TRABAJADOR": "DOCENTE",
                "SITUACION LABORAL": "NOMBRADO",
                "TIPO DE REGISTRO": "ORGANICA",
                "DOCUMENTO DE IDENTIDAD": dni,
                "APELLIDO PATERNO": "Ejemplo",
                "APELLIDO MATERNO": "Ficticio",
                "NOMBRES": f"Persona {numero}",
                "FECHA DE INICIO": date(2026, 1, 1),
                "CODIGO LOCAL": f"99991{numero}",
                "DISTRITO": "LUYA",
                "NIVEL EDUCATIVO": nivel,
                "NOMBRE DE LA INSTITUCION EDUCATIVA": nombre,
                "CARGO": "DOCENTE",
            }
        )
        archivos.append(
            construir_anexo3_minimo(
                tmp_path / f"reporte_{nivel}.xlsx",
                institucion_raw=nombre,
                nivel_raw=nivel.upper(),
                periodo=mes,
                personas=[
                    {
                        "dni": dni,
                        "nombres": f"Ejemplo Ficticio Persona {numero}",
                        "cargo": "DOCENTE",
                        "marcas": {1: "A", 2: "A", 3: "A", 4: "A", 5: "A"},
                    }
                ],
            )
        )
    cargar(client, construir_nexus_minimo(nexus, tmp_path / "nexus.xlsx"), "nexus")
    consulta = f"/revision?periodo=2026-09&nivel={nivel_filtro}"
    assert client.get(consulta).status_code == 200
    if modo == "cola":
        for nivel, archivo in zip(niveles, archivos, strict=True):
            respuesta = encolar(client, archivo, nivel=nivel_filtro)
            assert respuesta.status_code == 200
            assert respuesta.json["estado"] == "PROCESADO"
            reporte = respuesta.json["reportes"][0]
            assert nivel.upper() in reporte["etiqueta"].upper()
            assert "09/2026" in reporte["etiqueta"]
            assert client.get(reporte["url"]).status_code == 200
    else:
        respuesta = post(
            client,
            "/revision/cargar",
            {
                "tipo": "asistencia",
                "periodo": "2026-09",
                "nivel": nivel_filtro,
                "archivos": [(io.BytesIO(a.read_bytes()), a.name) for a in archivos],
            },
        )
        assert respuesta.status_code == 303
        assert respuesta.location == consulta + "#carga-reportes"
    reportes = conn.execute(
        """SELECT ie.cod_mod,ie.nivel_modalidad,r.periodo,
                  r.nivel_modalidad_reportada_raw,s.nivel_modalidad AS nivel_serie
           FROM reporte_asistencia r
           JOIN institucion_educativa ie USING(institucion_educativa_id)
           JOIN reporte_asistencia_serie s USING(reporte_asistencia_serie_id)
           ORDER BY ie.cod_mod"""
    ).fetchall()
    assert len(reportes) == 3
    for fila, fuente in zip(reportes, nexus, strict=True):
        assert fila["cod_mod"] == fuente["CODMOD I.E."]
        assert fila["periodo"] == mes
        assert fila["nivel_modalidad"] == fuente["NIVEL EDUCATIVO"]
        assert fila["nivel_serie"] == fila["nivel_modalidad"]
        assert fila["nivel_modalidad_reportada_raw"] == fila["nivel_modalidad"].upper()
    # Cada ámbito conserva únicamente su colegio y puede abrir su reporte.
    for numero, nivel in enumerate(niveles, 1):
        filas = instituciones_revision(conn, mes, nivel.upper())
        assert len(filas) == 1 and filas[0]["cod_mod"] == f"999910{numero}"
        assert len(filas[0]["reportes"]) == 1
    with client.session_transaction() as sesion:
        assert sesion["nivel"] == nivel_filtro
    html = client.get(consulta).text
    assert "El nivel seleccionado solo filtra la lista de instituciones" in html
    recientes = html.split("Mis últimas cargas", 1)[1].split(
        'id="revision-directory"', 1
    )[0]
    assert all(archivo.name in recientes for archivo in archivos)


def test_cola_identifica_fuente_sin_forzar_ambito_y_repeticion_no_duplica(
    client, conn, tmp_path
):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    op = str(uuid.uuid4())
    primero = encolar(client, archivo, operacion=op)
    assert primero.status_code == 200
    resultado = primero.json
    assert resultado["estado"] == "PROCESADO" and not resultado["duplicado"]
    assert "07/2026" in resultado["reportes"][0]["etiqueta"]
    assert (
        "Primaria" in resultado["reportes"][0]["etiqueta"]
        or "PRIMARIA" in resultado["reportes"][0]["etiqueta"]
    )
    assert "9999001" in resultado["reportes"][0]["etiqueta"]
    for key in ("url", "continuar_url"):
        assert client.get(resultado[key]).status_code == 200
    assert client.get(resultado["reportes"][0]["url"]).status_code == 200
    segundo = encolar(client, archivo, operacion=op)
    assert segundo.json["duplicado"]
    assert segundo.json["carga_id"] == resultado["carga_id"]
    assert (
        conn.execute("SELECT count(*) n FROM reporte_asistencia").fetchone()["n"] == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_recepcion r JOIN web_carga c USING(web_carga_id) WHERE c.tipo='asistencia'"
        ).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_carga_intento i JOIN web_carga c USING(web_carga_id) WHERE c.tipo='asistencia'"
        ).fetchone()["n"]
        == 1
    )
    page = client.get("/revision?periodo=2026-07&nivel=PRIMARIA")
    assert (
        "Cargar reportes mensuales" in page.text and "Mis últimas cargas" in page.text
    )
    assert page.text.index('id="carga-reportes"') < page.text.index(
        'id="revision-directory"'
    )


def test_archivo_sin_estructura_deriva_a_lectura_asistida(client, conn, tmp_path):
    nx, _, _ = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    archivo = tmp_path / "reporte_sin_tabla.xlsx"
    libro = openpyxl.Workbook()
    libro.active["A1"] = "Reporte ficticio sin estructura conocida"
    libro.save(archivo)
    response = encolar(client, archivo)
    assert response.status_code == 200
    assert response.json["estado"] == "PARCIAL"
    assert response.json["continuar_label"] == "Continuar lectura asistida"
    assert client.get(response.json["continuar_url"]).status_code == 200
    assert conn.execute("SELECT count(*) n FROM cierre_documento").fetchone()["n"] == 1


def test_error_de_formato_conserva_original_y_el_siguiente_archivo_se_procesa(
    client, conn, tmp_path
):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    malo = tmp_path / "corrupto.xlsx"
    malo.write_bytes(b"original ficticio no es un xlsx")
    response = encolar(client, malo)
    assert response.status_code == 200
    assert response.json["estado"] == "NO_SOPORTADO"
    assert client.get(response.json["url"] + "/original").data == malo.read_bytes()
    assert encolar(client, archivo).json["estado"] == "PROCESADO"
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_carga WHERE tipo='asistencia'"
        ).fetchone()["n"]
        == 2
    )


def test_original_recibido_sin_nexus_se_recupera_desde_resultado(
    client, conn, tmp_path
):
    _, _, archivo = fuentes(tmp_path)
    response = encolar(client, archivo)
    assert response.status_code == 200
    assert response.json["estado"] == "RECIBIDO"
    assert "NEXUS" in response.json["mensaje"]
    assert client.get(response.json["url"] + "/original").data == archivo.read_bytes()


@pytest.mark.parametrize(
    "datos", [{"tipo": "nexus"}, {"tipo": "calendario"}, {"archivos": []}]
)
def test_endpoint_cola_rechaza_otros_tipos_y_envio_vacio(client, conn, datos):
    response = post(
        client, "/revision/cargar", {"tipo": "asistencia", **datos}, headers=HEADERS
    )
    assert response.status_code == 400
    assert "error" in response.json
    assert conn.execute("SELECT count(*) n FROM web_recepcion").fetchone()["n"] == 0


def test_fallback_html_admite_lote_y_regresa_a_revision(client, conn, tmp_path):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    response = post(
        client,
        "/revision/cargar",
        {
            "tipo": "asistencia",
            "periodo": "2026-07",
            "nivel": "PRIMARIA",
            "archivos": [
                (io.BytesIO(b"ficticio"), "nota.txt"),
                (io.BytesIO(archivo.read_bytes()), archivo.name),
            ],
        },
    )
    assert response.status_code == 303
    assert (
        response.location == "/revision?periodo=2026-07&nivel=PRIMARIA#carga-reportes"
    )
    html = client.get(response.location).text
    assert (
        "nota.txt" in html
        and "asistencia.xlsx" in html
        and "Mis últimas cargas" in html
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_carga WHERE tipo='asistencia'"
        ).fetchone()["n"]
        == 2
    )


def test_cola_bloqueada_no_pierde_archivo_y_reintento_misma_operacion(
    client, conn, tmp_path
):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    op = str(uuid.uuid4())
    conn.execute("SELECT pg_advisory_lock(%s)", (WRITE_LOCK,))
    try:
        bloqueado = encolar(client, archivo, operacion=op)
        assert bloqueado.status_code == 409 and "curso" in bloqueado.json["error"]
        assert (
            conn.execute(
                "SELECT count(*) n FROM web_carga WHERE tipo='asistencia'"
            ).fetchone()["n"]
            == 0
        )
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (WRITE_LOCK,))
        conn.commit()
    assert encolar(client, archivo, operacion=op).json["estado"] == "PROCESADO"


def test_recientes_pertenecen_al_operador_y_una_fila_por_contenido(
    client, conn, tmp_path
):
    nx, _, archivo = fuentes(tmp_path)
    cargar(client, nx, "nexus")
    encolar(client, archivo)
    encolar(client, archivo)  # otra recepción, mismo contenido
    owner = conn.execute(
        "SELECT usuario_id FROM usuario WHERE email='operador@example.invalid'"
    ).fetchone()["usuario_id"]
    otro = crear_usuario(
        conn, "Otro operador ficticio", "otro@example.invalid", "OtraClaveFicticia_2026"
    )
    assert len(cargas_recientes(conn, owner)) == 1
    assert cargas_recientes(conn, otro) == []


def test_cola_exige_csrf_y_sesion_y_no_recibe_sin_autor(client, app, conn):
    response = client.post(
        "/revision/cargar", data={"tipo": "asistencia"}, headers=HEADERS
    )
    assert response.status_code == 400
    anonimo = app.test_client()
    anonimo.get("/ingresar")
    response = post(
        anonimo, "/revision/cargar", {"tipo": "asistencia"}, headers=HEADERS
    )
    assert response.json["redirect"].startswith("/ingresar")
    assert conn.execute("SELECT count(*) n FROM web_recepcion").fetchone()["n"] == 0


def test_cola_json_exige_un_archivo_por_peticion(client, conn):
    response = post(
        client,
        "/revision/cargar",
        {
            "tipo": "asistencia",
            "archivos": [(io.BytesIO(b"a"), "a.txt"), (io.BytesIO(b"b"), "b.txt")],
        },
        headers=HEADERS,
    )
    assert response.status_code == 400 and "error" in response.json
    assert conn.execute("SELECT count(*) n FROM web_recepcion").fetchone()["n"] == 0
