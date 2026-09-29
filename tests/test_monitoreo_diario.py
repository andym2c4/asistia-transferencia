"""Cruces con categorías locales, independencia del pago, cortes y modelo reproducible."""

import copy
import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

import joblib
import numpy as np
import pytest

from asistia.monitoreo.cortes import crear, perfiles_corte
from asistia.monitoreo.datos import cargar_fuentes, clasificador, construir
from asistia.monitoreo.modelo import entrenar, inferir, particion, perturbacion
from asistia.monitoreo.reglas import agregar, declaracion, evaluar

from .test_web import app, client, post  # noqa: F401


def cal(actividad=True, pago=True):
    return {
        "tipo_dia": "Lectivo" if actividad else "Feriado",
        "grupo_actividad": "LECTIVO" if actividad else "NO_LECTIVO_NI_GESTION",
        "es_remunerado": pago,
    }


def asis(codigo="A", pago=True):
    return {
        "tipo_dia": {
            "A": "Asistencia",
            "L": "Licencia con goce",
            "F": "Feriado",
            "I": "Inasistencia injustificada",
        }.get(codigo, codigo),
        "estado_asistencia_codigo": codigo,
        "es_remunerado": pago,
        "es_falta": codigo in {"I", "L", "F"},
    }


@pytest.mark.parametrize(
    "a,p,codigo,resultado",
    [
        (False, None, "A", ["PRESENCIA_SIN_ACTIVIDAD"]),
        (False, False, "A", ["PRESENCIA_SIN_ACTIVIDAD", "REMUNERACION_CONTRADICTORIA"]),
        (True, True, "L", []),
        (False, True, "L", []),
        (True, True, "F", ["NO_LABORABLE_CON_ACTIVIDAD"]),
        (False, False, "F", []),
        (False, False, "I", ["INASISTENCIA_SIN_ACTIVIDAD"]),
        (True, True, "A", []),
    ],
)
def test_reglas_separan_actividad_pago_y_licencia(a, p, codigo, resultado):
    r = evaluar(cal(a, p), asis(codigo, codigo not in {"I", "F"}))
    assert r["alertas"] == resultado
    assert "es_falta" not in r and "es_remunerado" not in r


def test_pendiente_conflicto_y_feriado_no_se_deciden_por_letra():
    assert declaracion({"tipo_dia": "X"}) is None
    assert (
        declaracion(
            {"tipo_dia": "dato desconocido", "codigo": "A", "es_remunerado": True}
        )
        is None
    )
    assert evaluar(cal(False, False), {}, impedimento="SIN_FUENTE")["alertas"] == []
    assert (
        evaluar(cal(False, False), dict(asis(), fuente="CONFLICTO_CATALOGO"))["alertas"]
        == []
    )
    assert evaluar({}, asis())["cruce_evaluable"] is False


def fuente_minima():
    rid, tid, aid, cid = (uuid4() for _ in range(4))
    r = {
        "reporte_asistencia_id": rid,
        "institucion_educativa_id": 1,
        "periodo": date(2026, 7, 1),
        "cod_mod": "9999001",
        "anexo": "0",
        "nombre_ie": "Escuela ficticia",
        "nivel": "PRIMARIA",
        "clasificacion_codigos": {"X": asis()},
    }
    return {
        "reportes": [r],
        "trabajadores": [
            {
                "reporte_asistencia_id": rid,
                "trabajador_en_reporte_id": tid,
                "trabajador_id": 7,
                "rol_laboral_id": 1,
                "vinculo_trabajador_ie_id": 10,
                "fila_detalle_origen": 12,
            }
        ],
        "dias": [
            {
                "asistencia_dia_id": aid,
                "trabajador_en_reporte_id": tid,
                "fecha": date(2026, 7, 1),
                "codigo_reportado_raw": "X",
            }
        ],
        "vigencias": [
            {"vid": 10, "fecha": date(2026, 7, n), "esperado": True}
            for n in range(1, 32)
        ],
        "calendarios": [
            {
                "institucion_educativa_id": 1,
                "calendarizacion_version_id": cid,
                "clasificacion_codigos": {"F": cal(False, None)},
                "procedencia_extraccion": {},
            }
        ],
        "programacion": [
            {
                "calendarizacion_version_id": cid,
                "fecha": date(2026, 7, 1),
                "codigo_reportado_raw": "F",
            }
        ],
        "catalogo": [],
        "equivalencias": [],
    }


def test_deduplicar_persona_rol_dia_y_preservar_fuentes():
    corte = fuente_minima()
    copia = copy.deepcopy(corte)
    tid2 = uuid4()
    corte["trabajadores"].append(
        dict(corte["trabajadores"][0], trabajador_en_reporte_id=tid2)
    )
    corte["dias"].append(
        dict(corte["dias"][0], trabajador_en_reporte_id=tid2, asistencia_dia_id=uuid4())
    )
    p = construir(corte)[0]
    assert len(p["datos"]["dias"]) == 31 and p["datos"]["observaciones_unidas"] == 31
    assert p["datos"]["dias_con_alerta"] == 1
    assert len(p["datos"]["dias"][0]["fuentes"]) == 2
    assert construir(copia)[0]["datos"]["dias_con_alerta"] == 1
    assert corte["dias"][0] == copia["dias"][0]


def test_dos_declaraciones_contradictorias_se_excluyen_del_cruce():
    corte = fuente_minima()
    r2 = copy.deepcopy(corte["reportes"][0])
    r2["reporte_asistencia_id"] = uuid4()
    r2["clasificacion_codigos"]["X"] = asis("I", False)
    corte["reportes"].append(r2)
    tid2 = uuid4()
    corte["trabajadores"].append(
        dict(
            corte["trabajadores"][0],
            reporte_asistencia_id=r2["reporte_asistencia_id"],
            trabajador_en_reporte_id=tid2,
        )
    )
    corte["dias"].append(dict(corte["dias"][0], trabajador_en_reporte_id=tid2))
    p = construir(corte)[0]["datos"]
    assert p["dias_con_alerta"] == 0
    assert p["pendientes"]["FUENTES_DIARIAS_CONTRADICTORIAS"] == 1


def test_fila_mensual_sin_observacion_no_contradice_una_declaracion_diaria():
    c = fuente_minima()
    c["trabajadores"].append(
        dict(c["trabajadores"][0], trabajador_en_reporte_id=uuid4())
    )
    p = construir(c)[0]["datos"]
    assert p["dias_con_alerta"] == 1
    assert p["cruces_evaluables"] == 1
    assert p["pendientes"].get("FUENTES_DIARIAS_CONTRADICTORIAS", 0) == 0
    assert len(p["dias"][0]["fuentes"]) == 2


def test_sin_calendario_derivado_vigencia_y_blancos_visibles():
    c = fuente_minima()
    p = construir(c)[0]["datos"]
    assert p["componentes"]["tasa_presencia_sin_actividad"]["valor"] == 1
    assert p["componentes"]["tasa_no_laborable_con_actividad"]["valor"] is None
    assert p["componentes"]["fraccion_asistencia_desconocida"]["numerador"] == 30
    c["calendarios"][0]["procedencia_extraccion"] = {"tipo": "DERIVADO_2025"}
    p = construir(c)[0]["datos"]
    assert p["dias_con_alerta"] == 0 and not p["apto_modelo"]
    assert p["pendientes"]["CALENDARIO_DERIVADO_2025"] == 31
    c["calendarios"] = []
    assert construir(c)[0]["datos"]["pendientes"]["SIN_CALENDARIO"] == 31
    c["trabajadores"][0]["vinculo_trabajador_ie_id"] = None
    assert construir(c)[0]["datos"]["excluidas"] == 31


def test_feriado_vertical_y_correccion_diaria_tienen_precedencia():
    c = fuente_minima()
    c["reportes"][0]["clasificacion_codigos"]["FERIADO"] = asis("F", False)
    c["dias"][0].update(codigo_reportado_raw="A", codigo_interpretado="FERIADO")
    assert construir(c)[0]["datos"]["dias_con_alerta"] == 0
    c["dias"][0]["evidencia_interpretacion"] = {
        "clasificacion_aceptada": asis("A", True)
    }
    assert construir(c)[0]["datos"]["dias_con_alerta"] == 1
    c["dias"][0].update(codigo_reportado_raw=None, codigo_interpretado=None)
    assert construir(c)[0]["datos"]["dias_con_alerta"] == 1


def test_catalogo_vigente_solo_en_copia_analitica_y_excepcion_local():
    r = {
        "categoria_id": 3,
        "dominio": "asistencia",
        "nombre": "Asistencia",
        "es_remunerado": True,
        "es_falta": False,
        "grupo_actividad": None,
        "categoria_version_id": uuid4(),
        "version": 2,
        "motivo": "Prueba ficticia",
        "autor": 1,
        "creado_en": datetime.now(UTC),
    }
    c = {
        "catalogo": [r],
        "equivalencias": [
            {"dominio": "asistencia", "significado": "ASISTENCIA", "categoria_id": 3}
        ],
    }
    fuente = dict(asis("A", None), fuente="LEYENDA_DOCUMENTAL")
    clasificar = clasificador(c)
    assert clasificar(fuente, "asistencia")["es_remunerado"] is True
    assert fuente["es_remunerado"] is None
    local = dict(fuente, fuente="REVISION_WEB", es_remunerado=False)
    assert clasificar(local, "asistencia") == local


def perfiles_ficticios(n=100):
    rng = np.random.default_rng(92)
    perfiles = []
    for i in range(n):
        dias = []
        for j in range(31):
            cv = cal(j % 7 < 5, j % 7 < 5)
            codigo = "A" if cv["es_remunerado"] else "F"
            if rng.random() < i % 11 / 55:
                codigo = rng.choice(["A", "I", "L", "F"])
            ca = asis(codigo, codigo in {"A", "L"})
            dias.append(
                {
                    "clave": f"{i}:{j}",
                    "fecha": f"2026-07-{j + 1:02}",
                    "calendario": cv,
                    "asistencia": ca,
                    "exclusion": None,
                    **evaluar(cv, ca),
                }
            )
        identidad = {
            "cod_mod": f"{9900000 + i:07}",
            "anexo": "0",
            "nombre_ie": f"Escuela ficticia {i}",
            "nivel": "PRIMARIA",
        }
        perfiles.append(
            {
                "perfil_id": str(uuid5(NAMESPACE_URL, f"ficticio:{i}")),
                "identidad": identidad,
                "institucion_educativa_id": i + 1,
                "periodo": "2026-07-01",
                "datos": {**agregar(dias), "dias": dias},
            }
        )
    return perfiles


def test_particion_estable_por_ie_con_meses_juntos():
    p = perfiles_ficticios(100)
    assert {particion(r["identidad"]) for r in p} == {
        "ENTRENAMIENTO",
        "VALIDACION",
        "PRUEBA",
    }
    assert particion(p[0]["identidad"]) == particion(
        dict(p[0]["identidad"], nombre_ie="Nombre corregido")
    )


def test_perturbacion_conserva_denominadores_y_originales():
    p = perfiles_ficticios(1)[0]
    antes = copy.deepcopy(p)
    copia, cambios = perturbacion(p, "presencia_sin_actividad")
    assert cambios and p == antes
    assert (
        copia["datos"]["features"]["tasa_presencia_sin_actividad"]
        > p["datos"]["features"]["tasa_presencia_sin_actividad"]
    )
    for f in p["datos"]["componentes"]:
        assert (
            copia["datos"]["componentes"][f]["denominador"]
            == p["datos"]["componentes"][f]["denominador"]
        )


def test_modelo_entrena_reserva_reproduce_inferencia_y_ablacion():
    ps = perfiles_ficticios()
    modelo, informe, resultados = entrenar(ps)
    assert informe["estado"] == "ENTRENADO" and informe["utilidad_RRHH"] == "NO_MEDIDO"
    assert hashlib.sha256(modelo).hexdigest() == informe["modelo_sha256"]
    assert np.allclose(inferir(modelo, ps), [r["score_if"] for r in resultados])
    ids = [set(g["ids"]) for g in informe["particiones"].values()]
    assert all(not (ids[i] & ids[j]) for i in range(3) for j in range(i))
    paquete = joblib.load(io.BytesIO(modelo))
    assert (
        paquete["completo"]["pipeline"].named_steps["modelo"]._n_samples
        == informe["particiones"]["ENTRENAMIENTO"]["perfiles"]
    )
    assert len(paquete["basico"]["features"]) == 4
    for e in informe["prueba"]:
        assert set(e["metodos"]) == {"if_cruces", "if_basico", "referencia", "reglas"}
        assert len({m["cupo"] for m in e["metodos"].values()}) == 1
        assert all(
            m["copias_en_cupo"] <= min(m["cupo"], e["copias"])
            for m in e["metodos"].values()
        )


def test_sin_modelo_no_fabrica_scores():
    modelo, informe, resultados = entrenar(perfiles_ficticios(2))
    assert modelo is None and not resultados and informe["estado"] == "SIN_MODELO"


def test_corte_con_fuentes_reales_de_fixture_idempotente_no_altera_documentos(
    conn, tmp_path
):
    from .test_leyendas_remuneracion import preparar_cruce

    preparar_cruce(conn, tmp_path)
    conn.commit()
    antes = cargar_fuentes(conn, 2026)
    cid, nuevo = crear(
        conn, 2026, autor="PRUEBA_FICTICIA", motivo="Corte de prueba ficticia"
    )
    conn.commit()
    assert nuevo
    assert crear(
        conn, 2026, autor="PRUEBA_FICTICIA", motivo="Reintento de prueba ficticia"
    ) == (cid, False)
    assert cargar_fuentes(conn, 2026) == antes
    corte = conn.execute(
        "SELECT * FROM monitoreo_diario_corte WHERE corte_id=%s", (cid,)
    ).fetchone()
    assert corte["manifiesto"]["evaluacion"]["estado"] == "SIN_MODELO"
    assert len(perfiles_corte(conn, cid)) == 1
    with zipfile.ZipFile(io.BytesIO(bytes(corte["paquete"]))) as z:
        hashes = json.loads(z.read("SHA256SUMS.json"))
        assert all(
            hashlib.sha256(z.read(k)).hexdigest() == h for k, h in hashes.items()
        )
        assert "modelo.joblib" not in z.namelist()


def test_web_entrada_vacio_csrf_filtros_fuente_y_descarga(client, app, conn, tmp_path):  # noqa: F811
    from .test_leyendas_remuneracion import preparar_cruce

    assert app.test_client().get("/monitoreo/diario").status_code == 302
    assert "Aún no hay un corte" in client.get("/monitoreo/diario").text
    assert (
        client.post("/monitoreo/diario/actualizar", data={"anio": 2026}).status_code
        == 400
    )
    assert (
        post(
            client,
            "/monitoreo/diario/actualizar",
            {"anio": "x", "motivo": "Motivo de prueba"},
        ).status_code
        == 400
    )
    preparar_cruce(conn, tmp_path)
    conn.commit()
    r = post(
        client,
        "/monitoreo/diario/actualizar",
        {"anio": 2026, "motivo": "Corte ficticio comprobable"},
    )
    assert r.status_code == 303
    assert "Modelo pendiente" in client.get(r.location).text
    assert client.get("/monitoreo").location.endswith("/monitoreo/diario")
    assert client.get("/monitoreo?fuente=dre").status_code == 200
    assert client.get(r.location + "&estado=NORMAL").status_code == 400
    assert client.get(r.location + "&mes=2030-01").status_code == 400
    pid = conn.execute("SELECT perfil_id FROM monitoreo_diario_perfil").fetchone()[
        "perfil_id"
    ]
    detalle = client.get(
        f"/monitoreo/diario/perfiles/{pid}?mes=2026-07&estado=TODOS&q=ficticia&pagina=2&mostrar=TODOS"
    )
    assert (
        detalle.status_code == 200
        and "Volver a Monitoreo con los mismos filtros" in detalle.text
    )
    assert "pagina=2" in detalle.text and "q=ficticia" in detalle.text
    cid = conn.execute("SELECT corte_id FROM monitoreo_diario_corte").fetchone()[
        "corte_id"
    ]
    assert client.get(f"/monitoreo/diario/cortes/{cid}/paquete.zip").status_code == 200
    assert client.get(f"/monitoreo/diario/perfiles/{uuid4()}").status_code == 404
