"""Cálculos del expediente: datos fijados, dos baselines y simulaciones explícitas."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

FEATURES = [
    "dias_justificados_por_registro_conocido",
    "dias_injustificados_por_registro_conocido",
    "horas_no_justificadas_por_registro_conocido",
    "fraccion_registros_con_injustificadas",
    "fraccion_conteos_desconocidos",
]
SEED = 20260915


def raiz():
    for p in [Path.cwd(), *Path.cwd().parents]:
        if (p / "datos/manifiesto.json").is_file():
            return p
        q = p / "data/entrega_rubrica/2026-09-15"
        if (q / "datos/manifiesto.json").is_file():
            return q
    raise FileNotFoundError(
        "Ejecuta desde la carpeta del expediente con datos/manifiesto.json."
    )


def cargar(root=None):
    root = Path(root) if root else raiz()
    m = json.loads((root / "datos/manifiesto.json").read_text())
    for nombre, h in m["hashes"].items():
        assert (
            hashlib.sha256((root / "datos" / nombre).read_bytes()).hexdigest() == h
        ), nombre
    df = pd.read_csv(root / "datos/perfiles.csv", float_precision="round_trip")
    assert not df.duplicated(["ie_alias", "periodo_fuente"]).any()
    assert len(df) == m["reportes"]
    assert set(df["naturaleza"]) == {"SINTETICO_DERIVADO_DRE"}
    assert df.ie_alias.nunique() == m["instituciones"]
    return df, m


def matriz(df):
    return df[FEATURES].to_numpy(dtype=float)


def ajustar(train, arboles=300, muestra=256, seed=SEED):
    p = Pipeline(
        [
            ("imputar", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "bosque",
                IsolationForest(
                    n_estimators=arboles,
                    max_samples=min(muestra, len(train)),
                    random_state=seed,
                    contamination="auto",
                    n_jobs=1,
                ),
            ),
        ]
    )
    x = matriz(train)
    p.fit(x)
    z = p.named_steps["imputar"].transform(x)
    med = np.median(z, axis=0)
    q25, q75 = np.quantile(z, [0.25, 0.75], axis=0)
    return {
        "pipeline": p,
        "mediana": med,
        "escala": np.where(q75 > q25, q75 - q25, 1.0),
    }


def puntuar(modelo, df):
    x = matriz(df)
    z = modelo["pipeline"].named_steps["imputar"].transform(x)
    return {
        "IF": -modelo["pipeline"].score_samples(x),
        "B1_mediana_IQR": np.max(
            np.abs(z - modelo["mediana"]) / modelo["escala"], axis=1
        ),
        # Referencia determinista interpretable; no suma días y horas ni decide pago.
        "B2_dias_injustificados": z[:, 1],
    }


def top(scores, proporcion=0.1):
    if not 0 < proporcion <= 1:
        raise ValueError("Cupo debe estar entre 0 y 1.")
    n = len(scores)
    if not n:
        return np.array([], dtype=int)
    return np.argsort(-np.asarray(scores), kind="stable")[: math.ceil(n * proporcion)]


def copias(df, patron="intenso", fraccion=0.2, seed=SEED):
    """Copias de vectores, no hechos mensuales ni días reconstruidos."""
    if df.empty:
        raise ValueError("No hay perfiles que perturbar.")
    if not 0 < fraccion <= 1:
        raise ValueError("Fracción inválida.")
    rng = np.random.default_rng(seed)
    ids = np.sort(
        rng.choice(len(df), max(1, math.ceil(len(df) * fraccion)), replace=False)
    )
    c = df.iloc[ids].copy(deep=True)
    if patron in {"leve", "intenso"}:
        dias, horas, fr = 0.0, 0.0, 0.0
        if patron == "leve":
            dias, horas, fr = 2, 0, 0.25
        else:
            dias, horas, fr = 10, 20, 0.8
        c[FEATURES[1]] = np.minimum(30, c[FEATURES[1]].fillna(0) + dias)
        c[FEATURES[2]] = c[FEATURES[2]].fillna(0) + horas
        c[FEATURES[3]] = np.maximum(fr, c[FEATURES[3]].fillna(0))
    elif patron == "solo_horas":
        c[FEATURES[2]] = c[FEATURES[2]].fillna(0) + 20
    elif patron == "datos_ausentes":
        c[FEATURES[0]] = np.nan
        c[FEATURES[1]] = np.nan
        c[FEATURES[3]] = np.nan
        c[FEATURES[4]] = 1.0
    else:
        raise ValueError("Patrón de estrés desconocido.")
    return ids, c


def estres(modelo, df, patron, seed=SEED, cupo=0.1):
    ids, c = copias(df, patron, seed=seed)
    originales = puntuar(modelo, df)
    alteradas = puntuar(modelo, c)
    resultado = []
    for nombre, s in originales.items():
        cs = alteradas[nombre]
        combinado = np.concatenate([s, cs])
        sel = top(combinado, cupo)
        aciertos = int(np.sum(sel >= len(df)))
        aumenta = int(np.sum(cs > s[ids]))
        k = len(sel)
        resultado.append(
            {
                "metodo": nombre,
                "patron": patron,
                "base_sin_etiqueta": len(df),
                "copias": len(c),
                "cupo": k,
                "copias_en_cupo": aciertos,
                "cobertura_copias": aciertos / len(c),
                "maximo_por_cupo": min(k, len(c)) / len(c),
                "pares_aumenta": aumenta,
                "pares_total": len(c),
                "precision_real": "NO_MEDIDO",
                "recall_real": "NO_MEDIDO",
            }
        )
    return pd.DataFrame(resultado)


def folds_temporales(df):
    """Bloques mensuales completos: nunca split por posiciones de filas mezcladas."""
    casos = [(["2026-03"], "2026-04"), (["2026-03", "2026-04"], "2026-05")]
    for train_meses, val_mes in casos:
        train = df[df.apto_modelo & df.periodo_fuente.isin(train_meses)].copy()
        val = df[df.apto_modelo & (df.periodo_fuente == val_mes)].copy()
        assert max(train.periodo_fuente) < min(val.periodo_fuente)
        assert "2026-06" not in set(train.periodo_fuente) | set(val.periodo_fuente)
        yield train_meses, val_mes, train, val


def buscar(df):
    """Cuatro configuraciones prefijadas; selección solo abril/mayo y patrón leve."""
    filas = []
    for arboles in [100, 300]:
        for muestra in [128, 256]:
            for train_meses, val_mes, train, val in folds_temporales(df):
                modelo = ajustar(train, arboles, muestra)
                r = estres(modelo, val, "leve", seed=SEED + int(val_mes[-2:]))
                fila = r[r.metodo == "IF"].iloc[0].to_dict()
                filas.append(
                    {
                        "arboles": arboles,
                        "muestra": muestra,
                        "train": ",".join(train_meses),
                        "validacion": val_mes,
                        **fila,
                    }
                )
    detalles = pd.DataFrame(filas)
    resumen = detalles.groupby(["arboles", "muestra"], as_index=False).agg(
        copias_en_cupo=("copias_en_cupo", "sum"), copias=("copias", "sum")
    )
    resumen["cobertura_copias"] = resumen.copias_en_cupo / resumen.copias
    resumen = resumen.sort_values(
        ["cobertura_copias", "arboles", "muestra"], ascending=[False, True, True]
    ).reset_index(drop=True)
    return detalles, resumen


def drift(train, actual):
    filas = []
    for f in FEATURES:
        a = train[f].dropna().to_numpy()
        b = actual[f].dropna().to_numpy()
        med = np.median(a) if len(a) else np.nan
        escala = np.quantile(a, 0.75) - np.quantile(a, 0.25) if len(a) else 1
        escala = escala if escala > 0 else 1.0
        filas.append(
            {
                "variable": f,
                "n_train": len(a),
                "n_actual": len(b),
                "mediana_train": med,
                "distancia_wasserstein_escalada": float(
                    wasserstein_distance(a, b) / escala
                )
                if len(a) and len(b)
                else None,
                "fraccion_nula_train": float(train[f].isna().mean()),
                "fraccion_nula_actual": float(actual[f].isna().mean()),
                "constante_train": bool(len(a) and np.ptp(a) == 0),
            }
        )
    return pd.DataFrame(filas)


def escenario(
    n_total=307,
    n_puntuable=200,
    cupo=0.1,
    prevalencia=0.1,
    recall=0.6,
    minutos_revision=12,
    fraccion_auditoria=0.1,
    minutos_operacion=30,
    coste_extra_fp=4,
    coste_extra_fn=90,
):
    """Matriz hipotética y esfuerzo de revisión, separados de ahorro observado M01."""
    if not 0 <= n_puntuable <= n_total:
        raise ValueError("Población incoherente.")
    if any(not 0 <= v <= 1 for v in [cupo, prevalencia, recall, fraccion_auditoria]):
        raise ValueError("Fracciones fuera de rango.")
    if any(
        v < 0
        for v in [minutos_revision, minutos_operacion, coste_extra_fp, coste_extra_fn]
    ):
        raise ValueError("Costos negativos.")
    k = math.ceil(n_puntuable * cupo)
    positivos = n_puntuable * prevalencia
    tp = positivos * recall
    if tp > k or k - tp > n_puntuable - positivos:
        raise ValueError("Recall/prevalencia incompatibles con el cupo.")
    fp = k - tp
    fn = positivos - tp
    tn = n_puntuable - positivos - fp
    excluidos = n_total - n_puntuable
    auditoria = math.ceil((n_puntuable - k) * fraccion_auditoria)
    # Auditoría uniforme de no seleccionados; rendimiento perfecto es supuesto explícito.
    captura_auditoria = fn * auditoria / (n_puntuable - k) if n_puntuable > k else 0
    fn_residual = fn - captura_auditoria
    revision_total = excluidos + k + auditoria
    manual = n_total * minutos_revision
    asistido = revision_total * minutos_revision + minutos_operacion
    riesgo = fp * coste_extra_fp + fn_residual * coste_extra_fn
    return {
        "naturaleza": "ESCENARIO_HIPOTETICO_NO_M01",
        "total_reportes": n_total,
        "puntuables": n_puntuable,
        "revision_datos_insuficientes": excluidos,
        "cupo": k,
        "positivos_supuestos": positivos,
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "precision_supuesta": tp / k if k else None,
        "recall_supuesto": tp / positivos if positivos else None,
        "auditoria_no_seleccionados": auditoria,
        "fn_residual_esperado": fn_residual,
        "horas_manual_hipoteticas": manual / 60,
        "horas_asistidas_hipoteticas": asistido / 60,
        "diferencia_horas_hipotetica": (manual - asistido) / 60,
        "horas_costo_riesgo_proxy": riesgo / 60,
        "horas_asistidas_mas_riesgo_proxy": (asistido + riesgo) / 60,
    }


def guardar_json(path, objeto):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(objeto, ensure_ascii=False, indent=2, default=str, allow_nan=False)
        + "\n"
    )
