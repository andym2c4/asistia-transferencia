"""RF-F01: directorio y movimientos, PostgreSQL aislado y fuentes ficticias."""

import uuid
from datetime import date

import pytest

from asistia.web.instituciones import directorio

from .test_web import app, client  # noqa: F401 — fixtures HTTP autenticadas

# ruff: noqa: F811


def institucion(conn, codigo="9999001", nivel="Primaria", anexo="0"):
    local = conn.execute(
        "INSERT INTO local_educativo(distrito) VALUES ('LUYA') RETURNING local_educativo_id"
    ).fetchone()["local_educativo_id"]
    return conn.execute(
        """INSERT INTO institucion_educativa(local_educativo_id,cod_mod,anexo,nombre_ie,nivel_modalidad,distrito,centro_poblado)
        VALUES (%s,%s,%s,%s,%s,'LUYA','Centro ficticio') RETURNING institucion_educativa_id""",
        (local, codigo, anexo, "Institución ficticia " + codigo, nivel),
    ).fetchone()["institucion_educativa_id"]


def vinculo(conn, iid, tid=None, fin=None):
    if tid is None:
        dni = str(
            99000000 + conn.execute("SELECT count(*) n FROM trabajador").fetchone()["n"]
        )
        tid = conn.execute(
            "INSERT INTO trabajador(dni,apellido_paterno,apellido_materno,nombres) VALUES (%s,'Ejemplo','Ficticio','Persona') RETURNING trabajador_id",
            (dni,),
        ).fetchone()["trabajador_id"]
    plaza = conn.execute(
        "INSERT INTO plaza(institucion_educativa_id,codigo_plaza) VALUES (%s,%s) RETURNING plaza_id",
        (iid, uuid.uuid4().hex[:20]),
    ).fetchone()["plaza_id"]
    vid = conn.execute(
        """INSERT INTO vinculo_trabajador_ie(trabajador_id,plaza_id,institucion_educativa_id,rol_laboral_id,situacion_laboral,tipo_registro,fecha_fin)
       VALUES (%s,%s,%s,(SELECT rol_laboral_id FROM catalogo_rol_laboral WHERE codigo='DOCENTE'),'NOMBRADO','ORGANICA',%s) RETURNING vinculo_trabajador_ie_id""",
        (tid, plaza, iid, fin),
    ).fetchone()["vinculo_trabajador_ie_id"]
    return tid, vid


def documento(conn, instante):
    oid = conn.execute(
        "INSERT INTO objeto_archivo(sha256,ruta_objeto,mime_type,tamano_bytes,primera_vez_visto_en) VALUES (%s,'/tmp/ficticio.xlsx','application/octet-stream',1,%s) RETURNING objeto_archivo_id",
        (uuid.uuid4().hex * 2, instante),
    ).fetchone()["objeto_archivo_id"]
    return conn.execute(
        "INSERT INTO documento_recibido(objeto_archivo_id,nombre_original) VALUES (%s,'Fuente ficticia.xlsx') RETURNING documento_recibido_id",
        (oid,),
    ).fetchone()["documento_recibido_id"]


def corte(
    conn,
    vinculos,
    *,
    instante="2026-09-15 12:00+00",
    fecha="2026-06-01",
    integridad="COMPLETA",
):
    did = documento(conn, instante)
    cid = conn.execute(
        """INSERT INTO nexus_carga(documento_recibido_id,fecha_corte,cargado_en,total_filas,filas_registradas,estado_integridad)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING nexus_carga_id""",
        (did, fecha, instante, len(vinculos), len(vinculos), integridad),
    ).fetchone()["nexus_carga_id"]
    for n, (tid, vid) in enumerate(vinculos, 1):
        conn.execute(
            """INSERT INTO nexus_registro(nexus_carga_id,fila_origen,cod_mod_ie_raw,codigo_plaza_raw,documento_identidad_raw,trabajador_id,vinculo_trabajador_ie_id,institucion_educativa_id,estado_resolucion)
            SELECT %s,%s,'9999001','FICTICIA','FICTICIO',%s,%s,institucion_educativa_id,'RESUELTO' FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id=%s""",
            (cid, n, tid, vid, vid),
        )
    return cid


def confirmar(
    conn,
    par,
    *,
    origen="AUTOMATICO_IMPORTACION",
    accion="CONFIRMA_INICIO",
    instante="2026-09-20 12:00+00",
    carga="2026-09-05 12:00+00",
    uid=None,
):
    tid, vid = par
    did = documento(conn, carga)
    rid = conn.execute(
        """INSERT INTO reporte_asistencia(institucion_educativa_id,documento_recibido_id,periodo,tipo_fuente,institucion_reportada_raw)
        SELECT institucion_educativa_id,%s,'2026-07-01','EXCEL_NATIVO','Institución ficticia' FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id=%s RETURNING reporte_asistencia_id""",
        (did, vid),
    ).fetchone()["reporte_asistencia_id"]
    ter = conn.execute(
        """INSERT INTO trabajador_en_reporte(reporte_asistencia_id,trabajador_id,vinculo_trabajador_ie_id,rol_laboral_id,nombres_reportados_raw,fila_detalle_origen)
        SELECT %s,%s,%s,rol_laboral_id,'Persona ficticia',1 FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id=%s RETURNING trabajador_en_reporte_id""",
        (rid, tid, vid, vid),
    ).fetchone()["trabajador_en_reporte_id"]
    conn.execute(
        """INSERT INTO vinculo_trabajador_ie_confirmacion(vinculo_trabajador_ie_id,accion,precision_fecha,periodo_confirmado,trabajador_en_reporte_id,motivo,origen,confirmado_por,confirmado_en)
        VALUES (%s,%s,'PERIODO_MENSUAL','2026-07-01',%s,'Prueba ficticia',%s,%s,%s)""",
        (vid, accion, ter, origen, uid, instante),
    )

    return ter


def test_directorio_deduplica_personas_y_conserva_anexo_niveles_y_vacios(conn):
    a = institucion(conn)
    b = institucion(conn, nivel="PRIMARIA", anexo="1")
    institucion(conn, "9999002", "Inicial - Jardín")
    institucion(conn, "9999003", "Inicial - Programa no escolarizado")
    institucion(conn, "9999004", "Administración")
    uno = vinculo(conn, a)
    vinculo(conn, a, uno[0])
    vinculo(conn, b, uno[0])
    d = directorio(conn)
    assert d["total_padron"] == 5 and d["conteos_nivel"] == {
        "PRIMARIA": 2,
        "INICIAL": 2,
        "OTROS": 1,
    }
    assert [i["personas"] for i in d["instituciones"][:2]] == [1, 1]
    assert d["mes_actualizado"] is None
    assert directorio(conn, nivel_ie="PRIMARIA")["total"] == 2
    assert directorio(conn, nivel_ie="OTROS")["total"] == 1
    assert directorio(conn, q="Centro ficticio")["total"] == 5
    assert directorio(conn, q="%")["total"] == 0
    assert len(directorio(conn, nivel_ie="INICIAL")["tipos"]) == 2


def test_movimientos_mes_carga_distinto_corte_reimportacion_y_cese_explicito(conn):
    iid = institucion(conn)
    primero = vinculo(conn, iid, fin="2026-05-31")
    misma_persona = vinculo(conn, iid, primero[0], fin="2026-05-31")
    futuro = vinculo(conn, iid, fin="2026-12-31")
    sin_fin = vinculo(conn, iid)
    corte(conn, [], fecha="2026-01-01", instante="2026-08-01 12:00+00")
    corte(conn, [primero, misma_persona, futuro, sin_fin])
    corte(conn, [primero, misma_persona, futuro], instante="2026-10-02 12:00+00")
    sept = directorio(conn, actualizado=date(2026, 9, 1))
    assert sept["nuevos"] == 3 and sept["bajas"] == 1
    assert sept["origenes"][0]["nuevos"] == 3  # COMPLETA legado con estado PROCESANDO
    octu = directorio(conn, actualizado=date(2026, 10, 1))
    assert octu["nuevos"] == 0 and octu["bajas"] == 0  # ausencia no es cese
    assert directorio(conn, actualizado=date(2026, 6, 1))["nuevos"] == 0


def test_carga_parcial_excluida_y_limite_mes_lima(conn):
    iid = institucion(conn)
    corte(conn, [], fecha="2026-01-01", instante="2026-08-01 12:00+00")
    corte(conn, [vinculo(conn, iid)], instante="2026-10-01 03:00+00")
    corte(conn, [vinculo(conn, iid)], integridad="PARCIAL")
    d = directorio(conn)
    assert d["mes_actualizado"] == date(2026, 9, 1)
    assert d["nuevos"] == 1 and len(d["cortes_mes"]) == 2


def test_revision_manual_acumula_mes_fuente_sin_duplicar_automatico(conn):
    iid = institucion(conn)
    nuevo = vinculo(conn, iid)
    otro = vinculo(conn, iid)
    uid = conn.execute(
        "INSERT INTO usuario(nombre,email) VALUES ('RRHH ficticio','rrhh@example.invalid') RETURNING usuario_id"
    ).fetchone()["usuario_id"]
    confirmar(conn, nuevo)
    confirmar(
        conn, nuevo, origen="MANUAL_RRHH", uid=uid, instante="2026-10-10 12:00+00"
    )
    confirmar(
        conn, nuevo, origen="MANUAL_RRHH", uid=uid, instante="2026-10-11 12:00+00"
    )
    confirmar(conn, otro, accion="CONFIRMA_CIERRE", origen="MANUAL_RRHH", uid=uid)
    d = directorio(conn)
    assert d["mes_actualizado"] == date(2026, 9, 1)
    assert d["nuevos"] == 1 and d["bajas"] == 1
    assert d["origenes"][1]["nuevos"] == 1 and d["origenes"][2]["nuevos"] == 0


def test_totales_filtrados_no_suman_persona_entre_instituciones(conn):
    a = institucion(conn)
    b = institucion(conn, "9999002", "Secundaria")
    p = vinculo(conn, a)
    q = vinculo(conn, b, p[0])
    corte(conn, [], fecha="2026-01-01", instante="2026-08-01 12:00+00")
    corte(conn, [p, q])
    assert directorio(conn)["nuevos"] == 1
    assert directorio(conn, nivel_ie="PRIMARIA")["nuevos"] == 1
    assert directorio(conn, nivel_ie="SECUNDARIA")["nuevos"] == 1
    assert directorio(conn, q="No existe")["nuevos"] == 0


def test_directorio_http_scroll_filtros_retorno_y_menu(client, conn):
    for n in range(105):
        institucion(conn, str(9999000 + n), "Primaria" if n < 101 else "Secundaria")
    conn.commit()
    r = client.get(
        "/instituciones?nivel_ie=PRIMARIA&pagina=3&q=Instituci&actualizado=2026-09"
    )
    assert r.status_code == 200
    assert r.text.count("data-institution-row") == 101
    assert "101 de 105 instituciones" in r.text
    assert "directory-pagination" not in r.text and "data-eight-rows" in r.text
    assert (
        r.text.index("Inicio</a>")
        < r.text.index("Revisión</a>")
        < r.text.index("Consolidado</a>")
        < r.text.index("Instituciones y personal</a>")
        < r.text.index("Reglas de código</a>")
        < r.text.index("Más herramientas")
    )
    assert "Centro poblado" in r.text and "Personal registrado" in r.text
    iid = conn.execute(
        "SELECT max(institucion_educativa_id) n FROM institucion_educativa WHERE nivel_modalidad='Primaria'"
    ).fetchone()["n"]
    detalle = client.get(
        f"/instituciones/{iid}?nivel_ie=PRIMARIA&pagina=3&q=Instituci&actualizado=2026-09"
    )
    assert detalle.status_code == 200
    assert "nivel_ie=PRIMARIA" in detalle.text and "q=Instituci" in detalle.text
    assert "No hay coincidencias" in client.get("/instituciones?q=SinCoincidencia").text
    assert (
        client.get("/instituciones?pagina=9999").text.count("data-institution-row")
        == 105
    )
    with client.session_transaction() as s:
        assert s["nivel"] == "PRIMARIA"  # filtros propios no cambian el ámbito global


@pytest.mark.parametrize("query", ["nivel_ie=INVALIDO", "actualizado=2026-99"])
def test_directorio_rechaza_filtros_invalidos(client, query):
    assert client.get("/instituciones?" + query).status_code == 400


def test_aceptar_alerta_del_reporte_cuenta_como_revision_rrhh(conn):
    from asistia.consolidado.revision import resolver_alerta

    iid = institucion(conn)
    par = vinculo(conn, iid)
    tid = confirmar(conn, par)
    uid = conn.execute(
        "INSERT INTO usuario(nombre,email) VALUES ('RRHH ficticio','aceptar@example.invalid') RETURNING usuario_id"
    ).fetchone()["usuario_id"]
    aid = conn.execute(
        """INSERT INTO validacion_reporte(reporte_asistencia_id,trabajador_en_reporte_id,codigo_regla,severidad,mensaje)
      SELECT reporte_asistencia_id,trabajador_en_reporte_id,'TRABAJADOR_SIN_NEXUS_VINCULADO_AUTOMATICAMENTE','ADVERTENCIA','Ejemplo ficticio'
      FROM trabajador_en_reporte WHERE trabajador_en_reporte_id=%s RETURNING validacion_reporte_id""",
        (tid,),
    ).fetchone()["validacion_reporte_id"]
    assert directorio(conn)["origenes"][2]["nuevos"] == 1
    resolver_alerta(conn, str(aid), uid, "Aceptado en prueba ficticia")
    d = directorio(conn)
    assert (
        d["nuevos"] == 1
        and d["origenes"][1]["nuevos"] == 1
        and d["origenes"][2]["nuevos"] == 0
    )
    assert d["mes_actualizado"] == date(2026, 9, 1)


def test_base_inicial_no_se_presenta_como_nuevos_aunque_se_cargue_despues(conn):
    iid = institucion(conn)
    antiguo = vinculo(conn, iid, fin="2025-12-31")
    nuevo = vinculo(conn, iid)
    corte(conn, [antiguo, nuevo], fecha="2026-06-01", instante="2026-09-05 12:00+00")
    corte(conn, [antiguo], fecha="2026-01-01", instante="2026-09-20 12:00+00")
    d = directorio(conn)
    assert d["base_inicial"] == 1 and d["nuevos"] == 1 and d["bajas"] == 0
