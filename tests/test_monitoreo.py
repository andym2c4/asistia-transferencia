"""Monitoreo IF: corte único, denominadores, filtros, exclusiones y navegación."""

from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from asistia.experimental.datos import redefinir
from asistia.experimental.modelo import ejecutar
from asistia.experimental.monitoreo import preparar_panel
from asistia.experimental.salidas import reportes_lote

from .test_experimental import lote_ficticio
from .test_web import app, client  # noqa: F401 -- fixtures compartidas


def ejemplo():
    reportes, scores = [], []
    for i in range(32):
        rid = str(uuid4())
        reportes.append(
            {
                "ml_reporte_mensual_id": rid,
                "periodo": date(2027, 2, 1),
                "periodo_fuente": date(2026, 6, 1),
                "nombre_ie": f"Institución ficticia {i}",
                "cod_mod": f"{i:07}",
                "anexo": "0",
                "nivel": "PRIMARIA" if i % 2 else "SECUNDARIA",
                "datos": {"apto_modelo": i != 31},
            }
        )
        if i != 31:
            scores.append(
                {
                    "reporte_id": rid,
                    "puesto": i + 1,
                    "en_cupo": i < 4,
                    "score_if": 0.8 - i / 100,
                }
            )
    return reportes, {
        "resultados": scores,
        "manifiesto": {"por_mes_origen": {"2026-06": {"cupo_propuesto": 4}}},
    }


def test_filtro_no_recalcula_ranking_cupo_ni_denominadores():
    filas, exp = ejemplo()
    original = deepcopy(filas)
    panel = preparar_panel(
        filas, exp, nivel="PRIMARIA", estado="PRIORIZADO", q="0000003"
    )
    assert panel["resumen"]["total"] == 16
    assert panel["resumen"]["puntuados"] == 15
    assert panel["resumen"]["priorizados"] == 2
    assert panel["resumen"]["sin_datos"] == 1
    assert panel["visibles"] == 1 and panel["filas"][0]["puntuacion"]["puesto"] == 4
    assert panel["referencias_mes"][0]["cupo_propuesto"] == 4
    assert panel["evolucion"][0]["total"] == 16
    assert panel["mes_elegido"] == "2027-02" and panel["meses_fuente"] == ["2026-06"]
    assert filas == original


def test_estados_sin_datos_sin_modelo_y_puntuacion_incompleta():
    filas, exp = ejemplo()
    sin_modelo = preparar_panel(filas, None)
    assert sin_modelo["resumen"]["sin_modelo"] == 31
    assert sin_modelo["resumen"]["sin_datos"] == 1
    assert sin_modelo["resumen"]["puntuados"] == 0
    exp["resultados"].pop()
    exp["resultados"][0]["score_if"] = float("nan")
    panel = preparar_panel(filas, exp, estado="TODOS")
    assert panel["resumen"]["sin_puntuacion"] == 2
    assert panel["resumen"]["puntuados"] == 29
    assert panel["resumen"]["total"] == 32
    excluido = preparar_panel(filas, exp, estado="SIN_DATOS")["filas"][0]
    assert excluido["puntuacion"] is None


def test_paginacion_vacia_y_limite_conserva_total():
    filas, exp = ejemplo()
    p = preparar_panel(filas, exp, estado="TODOS", pagina=999)
    assert p["pagina"] == 2 and p["paginas"] == 2 and len(p["filas"]) == 7
    vacio = preparar_panel(filas, exp, estado="TODOS", q="no existe")
    assert vacio["visibles"] == 0 and vacio["resumen"]["total"] == 32
    assert preparar_panel([], None)["resumen"]["total"] == 0


@pytest.mark.parametrize(
    "args",
    [
        {"mes": "2026-06"},
        {"nivel": "invalido"},
        {"estado": "NORMAL"},
        {"pagina": "a"},
        {"pagina": 0},
        {"q": "x" * 121},
    ],
)
def test_rechaza_ambitos_invalidos(args):
    filas, exp = ejemplo()
    with pytest.raises(ValueError):
        preparar_panel(filas, exp, **args)


def test_rechaza_resultados_duplicados_o_ajenos():
    filas, exp = ejemplo()
    exp["resultados"].append(exp["resultados"][0])
    with pytest.raises(ValueError, match="corte único"):
        preparar_panel(filas, exp)
    exp["resultados"][-1] = dict(exp["resultados"][0], reporte_id=str(uuid4()))
    with pytest.raises(ValueError, match="corte único"):
        preparar_panel(filas, exp)


def test_monitor_vacio_sin_modelo_y_control_acceso(client, app, conn, tmp_path):  # noqa: F811
    assert app.test_client().get("/monitoreo").status_code == 302
    r = client.get("/monitoreo")
    assert r.status_code == 200 and "Aún no hay perfiles" in r.text
    lid = lote_ficticio(conn, tmp_path / "corpus")
    r = client.get(f"/monitoreo?lote={lid}")
    assert r.status_code == 200 and "aún no tiene modelo ejecutado" in r.text
    assert "Sin datos suficientes" in r.text and "Sin modelo ejecutado" in r.text
    assert client.get("/monitoreo?lote=invalido").status_code == 400
    assert client.get(f"/monitoreo?lote={uuid4()}").status_code == 404
    assert client.get(f"/monitoreo?lote={lid}&mes=2027-02").status_code == 400
    # Sin una sesión no se accede tampoco a una fuente del monitoreo.
    rid = reportes_lote(conn, lid)[0]["ml_reporte_mensual_id"]
    assert app.test_client().get(f"/monitoreo/reportes/{rid}").status_code == 302


def test_monitor_conectado_conserva_corte_y_retorno(client, conn, tmp_path):  # noqa: F811
    lid = lote_ficticio(conn, tmp_path / "corpus")
    eid, _ = ejecutar(conn, lid)
    conn.commit()
    exp = conn.execute(
        "SELECT * FROM ml_experimento WHERE ml_experimento_id=%s", (eid,)
    ).fetchone()
    ruta = f"/monitoreo?lote={lid}&experimento={eid}&mes=2026-06&estado=TODOS"
    r = client.get(ruta)
    assert r.status_code == 200
    assert "Modelo entrenado" in r.text and "300 árboles" in r.text
    assert 'data-metrica="total">12<' in r.text
    assert 'data-metrica="sin_datos">1<' in r.text
    assert f"/experimental/experimentos/{eid}/paquete.zip" in r.text
    assert "variables fueron constantes" in r.text
    assert client.get(ruta + "&q=ausente").status_code == 200
    rid = reportes_lote(conn, lid)[0]["ml_reporte_mensual_id"]
    detalle = client.get(
        f"/monitoreo/reportes/{rid}?experimento={eid}&mes=2026-06&nivel_filtro=PRIMARIA&estado=TODOS&q=ficticia&pagina=2"
    )
    assert (
        detalle.status_code == 200
        and "Volver a Monitoreo con los mismos filtros" in detalle.text
    )
    assert (
        f"experimento={eid}" in detalle.text
        and "pagina=2" in detalle.text
        and "q=ficticia" in detalle.text
    )
    assert "Abrir documento de origen" in detalle.text
    # Un experimento posterior no altera la consulta explícita conservada.
    nuevo = conn.execute(
        "INSERT INTO ml_experimento(ml_lote_id,huella,manifiesto,modelo,resultados,autor) VALUES(%s,%s,%s,%s,%s,%s) RETURNING ml_experimento_id",
        (
            lid,
            "c" * 64,
            Jsonb(exp["manifiesto"]),
            exp["modelo"],
            Jsonb([]),
            "PRUEBA_FICTICIA",
        ),
    ).fetchone()["ml_experimento_id"]
    conn.commit()
    assert "Puesto " in client.get(ruta).text
    nuevo_panel = client.get(f"/monitoreo?lote={lid}&experimento={nuevo}&estado=TODOS")
    assert "Puntuación pendiente" in nuevo_panel.text
    hijo, _ = redefinir(
        conn, lid, "2027-11", autor="PRUEBA", motivo="Escenario ficticio"
    )
    conn.commit()
    assert client.get(f"/monitoreo?lote={hijo}&experimento={eid}").status_code == 400
    assert client.get(f"/monitoreo/reportes/{uuid4()}").status_code == 404
    intacto = conn.execute(
        "SELECT modelo,resultados,paquete FROM ml_experimento WHERE ml_experimento_id=%s",
        (eid,),
    ).fetchone()
    assert all(intacto[k] == exp[k] for k in intacto)
