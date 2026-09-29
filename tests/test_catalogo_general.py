"""Catálogo común: equivalencias, versiones fijas, excepciones y aplicación atómica."""

import html
import re
import uuid
from datetime import date

import psycopg
import pytest

from asistia import categorias as cat
from asistia.cierre.ingesta import aplicar_extraccion
from asistia.consolidado.generar import generar_consolidado
from asistia.leyendas import normalizar_leyenda
from asistia.web import ErrorDeTrabajo
from asistia.web.auth import crear_usuario
from asistia.web.catalogo import aplicar, documentos, preparar_alcance
from asistia.web.lecturas import huella_reporte
from asistia.web.leyendas import guardar_asistencia

from .test_cierre_integracion import base_ficticia
from .test_leyendas_remuneracion import (
    bloque_asistencia,
    extraida,
    persona_ficticia,
    preparar_cruce,
)
from .test_web import app, client, post  # noqa: F401

FILTROS = {"anio": 2026, "periodo": "", "nivel": "", "excepciones": False}


def operador(conn):
    row = conn.execute(
        "SELECT usuario_id FROM usuario WHERE rol='RRHH' LIMIT 1"
    ).fetchone()
    return (
        row["usuario_id"]
        if row
        else crear_usuario(
            conn,
            "Operador ficticio",
            "catalogo@example.invalid",
            "Prueba_Catalogo_2026",
        )
    )


def crear(conn, dominio="asistencia", nombre="Asistencia", pago="SI"):
    return cat.guardar_categoria(
        conn,
        {
            "dominio": dominio,
            "nombre": nombre,
            "remuneracion": pago,
            "es_falta": "NO",
            "grupo_actividad": "LECTIVO" if dominio == "calendario" else "",
            "motivo": "Regla ficticia de comprobación",
        },
        operador(conn),
        uuid.uuid4(),
    )


def asociar(conn, cid, significado="Asistencia"):
    c = cat.categoria(conn, cid)
    cat.asociar(
        conn,
        c["dominio"],
        significado,
        cid,
        "",
        "Equivalencia ficticia cotejada",
        operador(conn),
        uuid.uuid4(),
    )


def importar(conn, item, mes=7, codigo="X", significado="Asistencia"):
    b = bloque_asistencia(
        [persona_ficticia(1, [codigo] * 31)],
        [{"codigo": codigo, "descripcion": significado}],
    )
    b["mes"] = mes
    return aplicar_extraccion(conn, item, extraida(b))["ids"][0]


def clasificacion(conn, rid, codigo="X"):
    return conn.execute(
        "SELECT clasificacion_codigos FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
        (rid,),
    ).fetchone()["clasificacion_codigos"][codigo]


def test_equivalencia_por_significado_y_dominio_no_por_letra(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    cid = crear(conn)
    asociar(conn, cid)
    a = importar(conn, item)
    b = importar(conn, item, 8, "X", "Feriado")
    c = importar(conn, item, 10, "Z", "  ASISTÉNCIA  ")
    assert clasificacion(conn, a)["es_remunerado"] is True
    assert clasificacion(conn, b)["es_remunerado"] is None
    assert clasificacion(conn, c, "Z")["categoria_general"]["id"] == cid
    cal = cat.resolver_entrada(
        conn,
        "calendario",
        normalizar_leyenda(
            [{"codigo": "X", "descripcion": "Asistencia"}], "calendario"
        ),
    )
    assert "categoria_general" not in cal["X"]
    assert importar(conn, item) == a
    assert len(documentos(conn, "asistencia")) == 3


def test_cambio_de_regla_no_reescribe_documentos_y_nuevo_import_usa_version(
    conn, tmp_path
):
    _, item = base_ficticia(conn, tmp_path)
    cid = crear(conn)
    asociar(conn, cid)
    a = importar(conn, item)
    antes = clasificacion(conn, a)
    cat.guardar_categoria(
        conn,
        {
            "version": "1",
            "remuneracion": "NO",
            "es_falta": "NO",
            "motivo": "Regla ficticia posterior",
        },
        operador(conn),
        uuid.uuid4(),
        cid,
    )
    assert clasificacion(conn, a) == antes
    b = importar(conn, item, 8)
    assert clasificacion(conn, b)["es_remunerado"] is False
    assert clasificacion(conn, b)["categoria_general"]["version"] == 2
    assert clasificacion(conn, a)["categoria_general"]["version"] == 1


def test_pendiente_no_borra_fuente_explicita_y_conflicto_no_paga(conn):
    cid = crear(conn, nombre="Licencia", pago="")
    asociar(conn, cid, "Licencia con goce de remuneraciones")
    fuente = normalizar_leyenda(
        [{"codigo": "L", "descripcion": "Licencia con goce de remuneraciones"}],
        "asistencia",
    )
    c = cat.resolver_entrada(conn, "asistencia", fuente)["L"]
    assert c["es_remunerado"] is True
    assert c["clasificacion_fuente"] == fuente["L"]
    cat.guardar_categoria(
        conn,
        {
            "version": "1",
            "remuneracion": "NO",
            "motivo": "Regla contradictoria de prueba",
        },
        operador(conn),
        uuid.uuid4(),
        cid,
    )
    c = cat.resolver_entrada(conn, "asistencia", fuente)["L"]
    assert c["es_remunerado"] is None
    assert c["fuente"] == "CONFLICTO_CATALOGO"
    assert c["clasificacion_fuente"]["es_remunerado"] is True


def test_aplicar_varios_codigos_y_reintentar_no_duplica(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    a, b = importar(conn, item), importar(conn, item, 8, "Z")
    cid = crear(conn)
    asociar(conn, cid)
    uid = operador(conn)
    _regla, filas, excluidos, payload = preparar_alcance(conn, cid, FILTROS, uid)
    assert len(filas) == 2 and not excluidos
    assert clasificacion(conn, a)["es_remunerado"] is None
    resultado = aplicar(conn, payload, [a, b], "Aplicación de prueba revisada", uid)
    assert resultado == aplicar(
        conn, payload, [b, a], "Aplicación de prueba revisada", uid
    )
    assert resultado["documentos"] == 2
    assert clasificacion(conn, a)["tipo_dia"] == "Asistencia"
    assert clasificacion(conn, b, "Z")["categoria_general"]["id"] == cid
    assert (
        conn.execute(
            "SELECT count(*) n FROM web_revision_evento WHERE accion='CLASIFICAR_LEYENDA'"
        ).fetchone()["n"]
        == 2
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM leyenda_operacion WHERE accion='APLICAR'"
        ).fetchone()["n"]
        == 1
    )
    assert preparar_alcance(conn, cid, FILTROS, uid)[1] == []


def test_fuente_obsoleta_rechaza_todo_el_lote(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    a, b = importar(conn, item), importar(conn, item, 8)
    cid = crear(conn)
    asociar(conn, cid)
    uid = operador(conn)
    payload = preparar_alcance(conn, cid, FILTROS, uid)[3]
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION' WHERE reporte_asistencia_id=%s",
        (b,),
    )
    with pytest.raises(ErrorDeTrabajo, match="cambios posteriores"):
        aplicar(conn, payload, [a, b], "Intento sobre fuente obsoleta", uid)
    assert clasificacion(conn, a)["es_remunerado"] is None
    assert (
        conn.execute(
            "SELECT count(*) n FROM leyenda_operacion WHERE accion='APLICAR'"
        ).fetchone()["n"]
        == 0
    )


def test_regla_y_asociacion_obsoletas_rechazadas(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    a = importar(conn, item)
    cid = crear(conn)
    asociar(conn, cid)
    uid = operador(conn)
    payload = preparar_alcance(conn, cid, FILTROS, uid)[3]
    cat.guardar_categoria(
        conn,
        {"version": "1", "remuneracion": "NO", "motivo": "Regla concurrente ficticia"},
        uid,
        uuid.uuid4(),
        cid,
    )
    with pytest.raises(ValueError, match="cambiaron"):
        aplicar(conn, payload, [a], "Aplicación que debe rechazarse", uid)
    with pytest.raises(ValueError, match="regla cambió"):
        cat.guardar_categoria(
            conn,
            {"version": "1", "motivo": "Edición obsoleta ficticia"},
            uid,
            uuid.uuid4(),
            cid,
        )
    otro = crear(conn, nombre="Otra categoría")
    with pytest.raises(ValueError, match="equivalencia cambió"):
        cat.asociar(
            conn,
            "asistencia",
            "Asistencia",
            otro,
            "",
            "Asociación obsoleta ficticia",
            uid,
            uuid.uuid4(),
        )


def test_excepcion_local_requiere_inclusion_explicita(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    rid = importar(conn, item)
    uid = operador(conn)
    guardar_asistencia(
        conn,
        rid,
        {
            "codigo": "X",
            "tipo_dia": "Asistencia",
            "remuneracion": "NO",
            "es_falta": "NO",
        },
        "Excepción ficticia local",
        huella_reporte(conn, rid),
        uuid.uuid4(),
        uid,
    )
    cid = crear(conn)
    asociar(conn, cid)
    assert not preparar_alcance(conn, cid, FILTROS, uid)[1]
    assert "Revisión local" in preparar_alcance(conn, cid, FILTROS, uid)[2][0]["razon"]
    payload = preparar_alcance(conn, cid, {**FILTROS, "excepciones": True}, uid)[3]
    aplicar(conn, payload, [rid], "Sustituir excepción tras cotejo ficticio", uid)
    assert clasificacion(conn, rid)["es_remunerado"] is True


def test_calendario_nueva_version_y_salida_conservada(conn, tmp_path):
    _rid, cvid, _tid = preparar_cruce(conn, tmp_path)
    cons = generar_consolidado(conn, date(2026, 7, 1), "PRIMARIA")
    congelado = conn.execute(
        "SELECT fuente_calculo FROM consolidado_dre_detalle WHERE consolidado_dre_id=%s",
        (cons.consolidado_dre_id,),
    ).fetchall()
    cid = crear(conn, dominio="calendario", nombre="Lectivo", pago="SI")
    asociar(conn, cid, "Unidad de aprendizaje 1 remunerada")
    uid = operador(conn)
    _, filas, _, payload = preparar_alcance(conn, cid, FILTROS, uid)
    assert len(filas) == 1
    res = aplicar(
        conn, payload, [cvid], "Regla compartida para calendario ficticio", uid
    )
    nuevo = res["cambios"][0]["resultado"]
    assert nuevo != cvid
    assert (
        str(
            aplicar(
                conn, payload, [cvid], "Regla compartida para calendario ficticio", uid
            )["cambios"][0]["resultado"]
        )
        == nuevo
    )
    rows = conn.execute(
        "SELECT estado,clasificacion_codigos FROM calendarizacion_version ORDER BY version"
    ).fetchall()
    assert rows[-1]["estado"] == "BORRADOR"
    assert "categoria_general" not in rows[0]["clasificacion_codigos"]["U1"]
    assert rows[-1]["clasificacion_codigos"]["U1"]["categoria_general"]["id"] == cid
    assert (
        conn.execute(
            "SELECT fuente_calculo FROM consolidado_dre_detalle WHERE consolidado_dre_id=%s",
            (cons.consolidado_dre_id,),
        ).fetchall()
        == congelado
    )


def test_clasificacion_individual_aceptada_se_conserva(conn, tmp_path):
    from psycopg.types.json import Jsonb

    _, item = base_ficticia(conn, tmp_path)
    rid = importar(conn, item)
    aceptada = {
        "tipo_dia": "Licencia de prueba con goce",
        "es_remunerado": True,
        "es_falta": False,
    }
    conn.execute(
        "UPDATE asistencia_dia SET evidencia_interpretacion=%s WHERE fecha='2026-07-01'",
        (Jsonb({"clasificacion_aceptada": aceptada}),),
    )
    cid = crear(conn, pago="NO")
    asociar(conn, cid)
    uid = operador(conn)
    payload = preparar_alcance(conn, cid, FILTROS, uid)[3]
    aplicar(conn, payload, [rid], "Aplicación con excepción individual", uid)
    assert (
        conn.execute(
            "SELECT evidencia_interpretacion FROM asistencia_dia WHERE fecha='2026-07-01'"
        ).fetchone()["evidencia_interpretacion"]["clasificacion_aceptada"]
        == aceptada
    )


def test_versiones_inmutables_y_dominios_aislados(conn):
    cid = crear(conn)
    with pytest.raises(ValueError, match="mismo catálogo"):
        cat.asociar(
            conn,
            "calendario",
            "Asistencia",
            cid,
            "",
            "No mezclar dominios distintos",
            operador(conn),
            uuid.uuid4(),
        )
    with pytest.raises(psycopg.Error), conn.transaction():
        conn.execute(
            "UPDATE leyenda_categoria_version SET es_remunerado=false WHERE categoria_id=%s",
            (cid,),
        )


def test_web_catalogo_vista_previa_aplica_y_no_admite_token_alterado(
    client,  # noqa: F811
    conn,
    tmp_path,
):
    _, item = base_ficticia(conn, tmp_path)
    rid = importar(conn, item)
    conn.commit()
    response = post(
        client,
        "/categorias/guardar",
        {
            "dominio": "asistencia",
            "nombre": "Laborado",
            "remuneracion": "SI",
            "es_falta": "NO",
            "motivo": "Regla ficticia mediante interfaz",
        },
    )
    assert response.status_code == 303
    cid = int(response.location.rsplit("/", 1)[1])
    assert client.get("/categorias").status_code == 200
    assert (
        post(
            client,
            "/categorias/asociar",
            {
                "dominio": "asistencia",
                "descripcion": "Asistencia",
                "categoria_id": str(cid),
                "anterior": "",
                "motivo": "Coincidencia ficticia de significados",
            },
        ).status_code
        == 303
    )
    preview = client.get(f"/categorias/{cid}/alcance?anio=2026&mes=2026-07")
    assert preview.status_code == 200
    assert "1 documentos disponibles" in preview.text
    assert clasificacion(conn, rid)["es_remunerado"] is None
    token = html.unescape(re.search(r'name="alcance" value="([^"]+)"', preview.text)[1])
    assert (
        post(
            client,
            "/categorias/aplicar",
            {
                "alcance": token + "x",
                "seleccion": rid,
                "motivo": "Alteración que debe rechazarse",
            },
        ).status_code
        == 409
    )
    response = post(
        client,
        "/categorias/aplicar",
        {
            "alcance": token,
            "seleccion": [rid] * 50,
            "motivo": "Aplicación ficticia revisada",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 303
    assert "Aplicación del catálogo registrada" in client.get(response.location).text
    assert clasificacion(conn, rid)["es_remunerado"] is True
    assert "Categoría general: Laborado" in client.get(f"/reportes/{rid}").text


def test_web_preserva_formulario_y_requiere_csrf(client, conn):  # noqa: F811
    r = post(
        client,
        "/categorias/guardar",
        {"dominio": "asistencia", "nombre": "Asistencia", "motivo": ""},
        headers={"X-ASISTIA-Async": "1"},
    )
    assert r.status_code == 400 and "sustento" in r.text
    assert (
        client.post(
            "/categorias/guardar",
            data={"dominio": "asistencia", "nombre": "Sin autorización"},
        ).status_code
        == 400
    )
    assert client.get("/categorias?dominio=otra").status_code == 400
    assert conn.execute("SELECT count(*) n FROM leyenda_categoria").fetchone()["n"] == 0


def test_aplicar_una_categoria_no_modifica_otra_sin_vista_previa(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    b = bloque_asistencia(
        [persona_ficticia(1, ["A", "F"] + ["A"] * 29)],
        [
            {"codigo": "A", "descripcion": "Asistencia"},
            {"codigo": "F", "descripcion": "Feriado"},
        ],
    )
    rid = aplicar_extraccion(conn, item, extraida(b))["ids"][0]
    asistencia = crear(conn)
    feriado = crear(conn, nombre="Feriado")
    asociar(conn, asistencia)
    asociar(conn, feriado, "Feriado")
    uid = operador(conn)
    payload = preparar_alcance(conn, asistencia, FILTROS, uid)[3]
    aplicar(conn, payload, [rid], "Aplicar solo la categoría seleccionada", uid)
    assert clasificacion(conn, rid, "A")["es_remunerado"] is True
    assert clasificacion(conn, rid, "F")["es_remunerado"] is None
    assert "categoria_general" not in clasificacion(conn, rid, "F")


def test_retirar_equivalencia_conserva_historia_y_no_se_reutiliza(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    cid = crear(conn)
    asociar(conn, cid)
    rid = importar(conn, item)
    antes = clasificacion(conn, rid)
    uid, op = operador(conn), uuid.uuid4()
    cat.retirar_equivalencia(
        conn, "asistencia", "ASISTENCIA", cid, "Retirar equivalencia ficticia", uid, op
    )
    cat.retirar_equivalencia(
        conn, "asistencia", "ASISTENCIA", cid, "Retirar equivalencia ficticia", uid, op
    )
    assert clasificacion(conn, rid) == antes
    nuevo = importar(conn, item, 8)
    assert clasificacion(conn, nuevo)["es_remunerado"] is None
    assert not cat.equivalencias(conn, "asistencia")
    assert (
        conn.execute(
            "SELECT count(*) n FROM leyenda_operacion WHERE accion='RETIRAR_EQUIVALENCIA'"
        ).fetchone()["n"]
        == 1
    )
