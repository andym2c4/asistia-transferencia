"""Isolation Forest temporal y referencia simple; ninguna etiqueta real inventada."""

from __future__ import annotations

import hashlib
import io
import math
import platform
from pathlib import Path

import joblib
import numpy as np
import sklearn
from psycopg.types.json import Jsonb
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from .datos import FEATURES, huella

VERSION = "IF_MENSUAL_1"
SEMILLA = 20260915
PRESUPUESTO = 0.10


def matriz(filas):
    return np.array(
        [[r["datos"]["features"][f] for f in FEATURES] for r in filas], dtype=float
    )


def particiones(filas):
    meses = sorted({str(r["periodo_fuente"])[:7] for r in filas})
    if len(meses) < 4:
        raise ValueError(
            "Se necesitan al menos cuatro meses de origen para separar entrenamiento, validación y prueba."
        )
    mapa = {m: "ENTRENAMIENTO" for m in meses[:-2]}
    mapa.update({meses[-2]: "VALIDACION", meses[-1]: "PRUEBA"})
    grupos = {
        nombre: [
            r
            for r in filas
            if mapa[str(r["periodo_fuente"])[:7]] == nombre
            and r["datos"]["apto_modelo"]
        ]
        for nombre in ("ENTRENAMIENTO", "VALIDACION", "PRUEBA")
    }
    if len(grupos["ENTRENAMIENTO"]) < 20 or any(
        len(grupos[g]) < 5 for g in ("VALIDACION", "PRUEBA")
    ):
        raise ValueError(
            "El experimento requiere 20 perfiles utilizables de entrenamiento y 5 en cada período reservado."
        )
    return mapa, grupos


def configurar(filas):
    mapa, grupos = particiones(filas)
    return {
        "version": VERSION,
        "semilla": SEMILLA,
        "features": list(FEATURES),
        "particiones_origen": mapa,
        "n_estimators": 300,
        "max_samples": min(256, len(grupos["ENTRENAMIENTO"])),
        "contamination": "auto",
        "presupuesto_revision": PRESUPUESTO,
        "imputacion": "mediana entrenada; columna enteramente vacía=0 solo en matriz ML",
        "baseline": "max(abs(x-mediana_train)/IQR_train); IQR cero usa unidad 1",
        "python": platform.python_version(),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "joblib": joblib.__version__,
        "codigo_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def scores(paquete, x):
    pipeline = paquete["pipeline"]
    z = pipeline.named_steps["imputacion"].transform(x)
    base = np.max(np.abs(z - paquete["mediana"]) / paquete["escala"], axis=1)
    return -pipeline.score_samples(x), base


def sensibilidad(paquete, filas, semilla):
    """Copias perturbadas no vuelven al lote ni al ajuste. Base sin etiquetas."""
    rng = np.random.default_rng(semilla)
    x = matriz(filas)
    indices = sorted(
        rng.choice(len(x), size=max(1, math.ceil(len(x) * 0.2)), replace=False).tolist()
    )
    copias = x[indices].copy()
    cambios = []
    for j, i in enumerate(indices):
        # Estrés explícito de conteos, no una distribución real estimada.
        copias[j, 1] = min(30.0, (x[i, 1] if np.isfinite(x[i, 1]) else 0) + 10.0)
        copias[j, 2] = (x[i, 2] if np.isfinite(x[i, 2]) else 0) + 20.0
        copias[j, 3] = max(0.8, x[i, 3] if np.isfinite(x[i, 3]) else 0)
        cambios.append(
            {
                "reporte_base": str(filas[i]["ml_reporte_mensual_id"]),
                "antes": {
                    f: float(v) if np.isfinite(v) else None
                    for f, v in zip(FEATURES, x[i])
                },
                "despues": {
                    f: float(v) if np.isfinite(v) else None
                    for f, v in zip(FEATURES, copias[j])
                },
            }
        )
    original_scores = scores(paquete, x)
    copia_scores = scores(paquete, copias)
    resultado = {
        "naturaleza": "PRUEBA_DE_ESTRES_SINTETICA",
        "semilla": semilla,
        "registros_base_sin_etiqueta": len(x),
        "copias_inyectadas": len(copias),
        "cambios": cambios,
    }
    for metodo, original, copia in zip(
        ("isolation_forest", "referencia_simple"), original_scores, copia_scores
    ):
        combinado = np.concatenate([original, copia])
        k = math.ceil(len(combinado) * PRESUPUESTO)
        # Empates conservadores: registros de base antes que copias inyectadas.
        seleccion = np.argsort(-combinado, kind="stable")[:k]
        detectadas = int(np.sum(seleccion >= len(x)))
        resultado[metodo] = {
            "cupo_revision": k,
            "copias_en_cupo": detectadas,
            "cobertura_copias": {
                "numerador": detectadas,
                "denominador": len(copias),
                "valor": detectadas / len(copias),
            },
            "pares_score_aumenta": {
                "numerador": int(np.sum(copia > original[indices])),
                "denominador": len(copias),
            },
            "precision_real": "NO_MEDIDO",
            "recall_real": "NO_MEDIDO",
        }
    return resultado


def ajustar(filas, config):
    mapa, grupos = particiones(filas)
    train = grupos["ENTRENAMIENTO"]
    x = matriz(train)
    pipeline = Pipeline(
        [
            ("imputacion", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "modelo",
                IsolationForest(
                    n_estimators=config["n_estimators"],
                    max_samples=config["max_samples"],
                    contamination="auto",
                    random_state=config["semilla"],
                    n_jobs=1,
                ),
            ),
        ]
    )
    pipeline.fit(x)
    z = pipeline.named_steps["imputacion"].transform(x)
    mediana = np.median(z, axis=0)
    q25, q75 = np.quantile(z, [0.25, 0.75], axis=0)
    escala = np.where(q75 > q25, q75 - q25, 1.0)
    paquete = {
        "pipeline": pipeline,
        "mediana": mediana,
        "escala": escala,
        "features": list(FEATURES),
        "configuracion": config,
    }
    resultados = []
    por_mes = {}
    # Una sola configuración fijada de antemano; junio no cambia el ajuste.
    validacion = sensibilidad(paquete, grupos["VALIDACION"], SEMILLA + 1)
    prueba = sensibilidad(paquete, grupos["PRUEBA"], SEMILLA + 2)
    for periodo in sorted({str(r["periodo_fuente"])[:7] for r in filas}):
        disponibles = [
            r
            for r in filas
            if str(r["periodo_fuente"])[:7] == periodo and r["datos"]["apto_modelo"]
        ]
        if not disponibles:
            continue
        a, b = scores(paquete, matriz(disponibles))
        k = math.ceil(len(disponibles) * PRESUPUESTO)
        orden = sorted(
            range(len(a)),
            key=lambda i: (-a[i], disponibles[i]["institucion_educativa_id"]),
        )
        orden_b = sorted(
            range(len(b)),
            key=lambda i: (-b[i], disponibles[i]["institucion_educativa_id"]),
        )
        ranks = {i: rank + 1 for rank, i in enumerate(orden)}
        ranks_b = {i: rank + 1 for rank, i in enumerate(orden_b)}
        for i, fila in enumerate(disponibles):
            valor = matriz([fila])[0]
            desviaciones = [
                (f, float(v), float(m), float(abs(v - m) / s))
                for f, v, m, s in zip(FEATURES, valor, mediana, escala)
                if np.isfinite(v)
            ]
            desviaciones.sort(key=lambda d: (-d[3], d[0]))
            resultados.append(
                {
                    "reporte_id": str(fila["ml_reporte_mensual_id"]),
                    "particion": mapa[periodo],
                    "score_if": float(a[i]),
                    "score_referencia": float(b[i]),
                    "puesto": ranks[i],
                    "puesto_referencia": ranks_b[i],
                    "en_cupo": ranks[i] <= k,
                    "indicadores_a_revisar": [
                        {
                            "variable": f,
                            "valor": v,
                            "mediana_entrenamiento": m,
                            "desviacion_referencia": d,
                        }
                        for f, v, m, d in desviaciones[:3]
                    ],
                }
            )
        por_mes[periodo] = {
            "reportes_puntuados": len(disponibles),
            "cupo_propuesto": k,
            "coincidencias_en_cupo": len(set(orden[:k]) & set(orden_b[:k])),
            "rango_score_if": [float(min(a)), float(max(a))],
            "empates_en_umbral": int(sum(v == a[orden[k - 1]] for v in a)),
        }
    ids_train = {r["institucion_educativa_id"] for r in train}
    informe = {
        "configuracion": config,
        "estado": "VERIFICACION_SINTETICA",
        "exactitud_real": "NO_MEDIDO",
        "utilidad_RRHH": "NO_MEDIDO",
        "grano": "institucion_y_mes; denominadores de registros laborales conocidos",
        "particiones": {
            nombre: {
                "filas": len(rows),
                "ies": len({r["institucion_educativa_id"] for r in rows}),
                "ies_no_vistas_en_entrenamiento": len(
                    {r["institucion_educativa_id"] for r in rows} - ids_train
                ),
                "ids": [str(r["ml_reporte_mensual_id"]) for r in rows],
            }
            for nombre, rows in grupos.items()
        },
        "no_puntuados": [
            {
                "reporte_id": str(r["ml_reporte_mensual_id"]),
                "motivo": "SIN_CONTEOS_UTILIZABLES",
            }
            for r in filas
            if not r["datos"]["apto_modelo"]
        ],
        "preprocesamiento": {
            "medianas_imputacion": pipeline.named_steps[
                "imputacion"
            ].statistics_.tolist(),
            "mediana_referencia": mediana.tolist(),
            "escala_referencia": escala.tolist(),
            "variables_constantes_train": [
                f for i, f in enumerate(FEATURES) if np.ptp(z[:, i]) == 0
            ],
        },
        "por_mes_origen": por_mes,
        "validacion_sintetica": validacion,
        "prueba_sintetica": prueba,
        "limites": [
            "Corpus expuesto durante desarrollo; reserva temporal computacional, no validación independiente de campo.",
            "Guiones como cero son supuestos del lote sintético.",
            "Puntuación no es probabilidad de fraude ni decisión de pago.",
            "Indicadores muestran desviaciones respecto a referencia, no atribuciones causales del modelo.",
            "El cupo 10 % es propuesta de revisión y no prevalencia real de anomalías.",
            "Comparación con base sin etiquetas: no se calcula precisión ni recall reales.",
        ],
    }
    buffer = io.BytesIO()
    joblib.dump(paquete, buffer, compress=3)
    modelo = buffer.getvalue()
    informe["modelo_sha256"] = hashlib.sha256(modelo).hexdigest()
    return modelo, informe, resultados


def ejecutar(conn, lote_id, *, autor="AGENTE_TECNICO"):
    lote = conn.execute(
        "SELECT * FROM ml_lote WHERE ml_lote_id=%s", (lote_id,)
    ).fetchone()
    if not lote:
        raise ValueError("El lote solicitado no existe.")
    filas = conn.execute(
        "SELECT * FROM ml_reporte_mensual WHERE ml_lote_id=%s ORDER BY periodo_fuente,institucion_educativa_id",
        (lote_id,),
    ).fetchall()
    config = configurar(filas)
    hash_run = huella({"lote": lote["huella"], "configuracion": config})
    existente = conn.execute(
        "SELECT ml_experimento_id FROM ml_experimento WHERE huella=%s", (hash_run,)
    ).fetchone()
    if existente:
        return existente["ml_experimento_id"], False
    modelo, manifiesto, resultados = ajustar(filas, config)
    with conn.transaction():
        nuevo = conn.execute(
            """INSERT INTO ml_experimento(ml_lote_id,huella,manifiesto,modelo,resultados,autor)
          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(huella) DO NOTHING RETURNING ml_experimento_id""",
            (lote_id, hash_run, Jsonb(manifiesto), modelo, Jsonb(resultados), autor),
        ).fetchone()
        if nuevo:
            from .salidas import paquete_zip

            # Congelar el expediente dentro de la misma transacción del experimento.
            archivo = paquete_zip(conn, nuevo["ml_experimento_id"])
            conn.execute(
                "UPDATE ml_experimento SET paquete=%s WHERE ml_experimento_id=%s",
                (archivo, nuevo["ml_experimento_id"]),
            )
            return nuevo["ml_experimento_id"], True
        return conn.execute(
            "SELECT ml_experimento_id FROM ml_experimento WHERE huella=%s", (hash_run,)
        ).fetchone()["ml_experimento_id"], False


def inferir(modelo, filas):
    """Solo artefactos internos del servidor; nunca deserializar subidas de usuarios."""
    paquete = joblib.load(io.BytesIO(modelo))
    if paquete["configuracion"]["sklearn"] != sklearn.__version__:
        raise ValueError("Recupera el entorno registrado del modelo antes de inferir.")
    return scores(paquete, matriz(filas))
