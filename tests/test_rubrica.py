"""Invariantes de evaluación y costos, con perfiles ficticios sin datos privados."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location(
    "soporte_rubrica", Path(__file__).parents[1] / "notebooks/rubrica/soporte.py"
)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def perfiles():
    rng = np.random.default_rng(17)
    df = pd.DataFrame(rng.uniform(0, 1, (40, 5)), columns=s.FEATURES)
    df[s.FEATURES[2]] = 0.0
    df["periodo_fuente"] = np.repeat(["2026-03", "2026-04", "2026-05", "2026-06"], 10)
    df["apto_modelo"] = True
    return df


def test_excluidos_siempre_revisados_y_costos_sin_doble_conteo():
    r = s.escenario()
    assert sum(r[k] for k in ["TP", "FP", "FN", "TN"]) == 200
    assert r["revision_datos_insuficientes"] == 107
    assert r["auditoria_no_seleccionados"] == 18
    assert r["horas_asistidas_hipoteticas"] == 29.5
    assert r["fn_residual_esperado"] == 7.2
    assert r["horas_costo_riesgo_proxy"] == pytest.approx((8 * 4 + 7.2 * 90) / 60)
    assert (
        s.escenario(coste_extra_fp=0, coste_extra_fn=0)[
            "horas_asistidas_mas_riesgo_proxy"
        ]
        == 29.5
    )


@pytest.mark.parametrize(
    "kw",
    [
        {"n_puntuable": 308},
        {"recall": 1.1},
        {"minutos_revision": -1},
        {"prevalencia": 0.9, "recall": 1},
        {"prevalencia": 1, "recall": 0},
    ],
)
def test_rechaza_matriz_imposible(kw):
    with pytest.raises(ValueError):
        s.escenario(**kw)


def test_cero_puntuables_no_equivale_a_precision_cero():
    r = s.escenario(n_total=12, n_puntuable=0)
    assert r["revision_datos_insuficientes"] == 12
    assert r["precision_supuesta"] is None
    assert r["recall_supuesto"] is None
    assert r["FN"] == 0


def test_auditoria_total_no_deja_fn_residual():
    r = s.escenario(fraccion_auditoria=1)
    assert r["fn_residual_esperado"] == 0
    assert r["horas_asistidas_hipoteticas"] == r["horas_manual_hipoteticas"] + 0.5


def test_coste_de_omision_puede_eliminar_ventaja():
    r = s.escenario(coste_extra_fn=360)
    assert r["horas_asistidas_mas_riesgo_proxy"] > r["horas_manual_hipoteticas"]


def test_folds_por_mes_no_por_orden_de_filas():
    df = perfiles().sample(frac=1, random_state=3)
    for meses, mes, train, val in s.folds_temporales(df):
        assert set(train.periodo_fuente) == set(meses)
        assert set(val.periodo_fuente) == {mes}
        assert set(train.index).isdisjoint(val.index)
        assert "2026-06" not in set(train.periodo_fuente) | set(val.periodo_fuente)
        assert max(train.periodo_fuente) < min(val.periodo_fuente)


def test_estres_no_modifica_original_y_respeta_techo_del_cupo():
    df = perfiles()
    original = df.copy(deep=True)
    modelo = s.ajustar(df, arboles=20, muestra=32)
    for patron in ["leve", "intenso", "solo_horas", "datos_ausentes"]:
        r = s.estres(modelo, df, patron)
        assert (r.copias_en_cupo <= r.cupo).all()
        assert (r.cobertura_copias <= r.maximo_por_cupo).all()
        assert set(r.precision_real) == {"NO_MEDIDO"}
    pd.testing.assert_frame_equal(df, original)


def test_cambio_en_variable_constante_no_es_deteccion_if():
    df = perfiles()
    modelo = s.ajustar(df, arboles=20, muestra=32)
    ids, copia = s.copias(df, "solo_horas")
    a, b = s.puntuar(modelo, df), s.puntuar(modelo, copia)
    np.testing.assert_array_equal(a["IF"][ids], b["IF"])
    np.testing.assert_array_equal(
        a["B2_dias_injustificados"][ids], b["B2_dias_injustificados"]
    )
    assert np.all(b["B1_mediana_IQR"] > a["B1_mediana_IQR"][ids])


def test_imputacion_no_aprende_de_validacion():
    df = perfiles()
    train = df.iloc[:20].copy()
    train[s.FEATURES[0]] = 2.0
    modelo = s.ajustar(train, arboles=20, muestra=20)
    antes = modelo["pipeline"].named_steps["imputar"].statistics_.copy()
    val = df.iloc[20:].copy()
    val[s.FEATURES[0]] = 1_000_000
    s.puntuar(modelo, val)
    np.testing.assert_array_equal(
        antes, modelo["pipeline"].named_steps["imputar"].statistics_
    )
    assert antes[0] == 2.0


def test_empates_no_favorecen_copias_y_cupo_es_explicito():
    assert s.top(np.ones(240)).tolist() == list(range(24))
    assert s.top([]).size == 0
    with pytest.raises(ValueError):
        s.top([1], proporcion=0)
